"""Cleaning layer: restatement dedupe, tag-priority resolution, de-cumulation, Q4 derivation."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .config import KEEP_FISCAL_YEARS, TAG_MAP

log = logging.getLogger(__name__)

ANNUAL_DAYS = (350, 380)      # duration of a fiscal year (covers 52/53-week years)
QUARTER_DAYS = (80, 105)      # duration of a standalone quarter
# days between fiscal-year start and the end of Q1 / Q2 / Q3
OFFSET_BUCKETS = {1: (75, 110), 2: (165, 200), 3: (250, 290)}


@dataclass
class CleanData:
    annual_flows: pd.DataFrame      # ticker, metric, fiscal_year, start, end, val
    quarterly_flows: pd.DataFrame   # ticker, metric, fiscal_year, fq, end, val, source
    instants: pd.DataFrame          # ticker, metric, end, val
    fallback_log: pd.DataFrame      # where a non-preferred XBRL tag supplied data


def fiscal_year_of(end: pd.Timestamp) -> int:
    """Fiscal year label = calendar year of the FY end date (52/53-week years that end
    in the first days of January belong to the prior year)."""
    return end.year - 1 if (end.month == 1 and end.day <= 7) else end.year


def select_facts(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve duplicates and tag priority. Returns (selected facts, fallback log)."""
    df = raw.copy()
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c])

    # Keep a little more than the retained window so the oldest kept year can derive Q4.
    cutoff = df["end"].max() - pd.DateOffset(years=KEEP_FISCAL_YEARS + 1)
    df = df[df["end"] >= cutoff]

    # 1) The same period is repeated in later filings (comparatives / restatements).
    #    The most recently filed value wins.
    df = (df.sort_values("filed")
            .drop_duplicates(["ticker", "metric", "xbrl_tag", "start", "end"], keep="last"))

    # 2) The same period may exist under several tags: lowest priority index wins.
    df = (df.sort_values("priority")
            .drop_duplicates(["ticker", "metric", "start", "end"], keep="first"))

    fb = df[df["priority"] > 0]
    fallback_log = (fb.groupby(["ticker", "metric", "xbrl_tag"])
                      .agg(periods=("end", "count"), first_end=("end", "min"), last_end=("end", "max"))
                      .reset_index())
    return df.reset_index(drop=True), fallback_log


def _bucket(offset_days: int) -> int | None:
    for q, (lo, hi) in OFFSET_BUCKETS.items():
        if lo <= offset_days <= hi:
            return q
    return None


def derive_flows(flow: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn raw duration facts into annual values and discrete quarters.

    10-Q cash-flow items are year-to-date (3M / 6M / 9M, all sharing the fiscal-year
    start date) and the 10-K carries the 12M figure, so for each fiscal year:
        Q1 = YTD3   Q2 = YTD6 - YTD3   Q3 = YTD9 - YTD6   Q4 = FY - YTD9
    Income-statement items also report standalone 3M figures, which are used directly
    when present. Q4 is never filed, so it is always derived."""
    f = flow[flow["start"].notna()].copy()
    f["days"] = (f["end"] - f["start"]).dt.days
    annual, quarterly = [], []

    for (ticker, metric), g in f.groupby(["ticker", "metric"], sort=False):
        is_avg = TAG_MAP[metric]["kind"] == "avg"
        for fy in g[g["days"].between(*ANNUAL_DAYS)].itertuples():
            s, e = fy.start, fy.end
            fyear = fiscal_year_of(e)
            annual.append(dict(ticker=ticker, metric=metric, fiscal_year=fyear,
                               start=s, end=e, val=fy.val))
            if is_avg:
                continue

            cum, disc, ends = {4: fy.val}, {}, {4: e}
            for r in g[(g["end"] > s) & (g["end"] < e)].itertuples():
                q = _bucket((r.end - s).days)
                if q is None:
                    continue
                if r.start == s:                       # year-to-date row
                    cum[q], ends[q] = r.val, r.end
                elif QUARTER_DAYS[0] <= r.days <= QUARTER_DAYS[1]:   # standalone quarter
                    disc[q], ends[q] = r.val, r.end

            vals, src = {}, {}
            for q in (1, 2, 3):
                if q in disc:
                    vals[q], src[q] = disc[q], "reported"
                elif q in cum and (q == 1 or (q - 1) in cum):
                    vals[q] = cum[q] - (cum[q - 1] if q > 1 else 0.0)
                    src[q] = "reported" if q == 1 else "derived"
                elif q in cum and all(p in vals for p in range(1, q)):
                    vals[q] = cum[q] - sum(vals[p] for p in range(1, q))
                    src[q] = "derived"
            if all(q in vals for q in (1, 2, 3)):
                vals[4] = cum[4] - sum(vals[q] for q in (1, 2, 3))
                src[4] = "derived"
            elif 3 in cum:
                vals[4] = cum[4] - cum[3]
                src[4] = "derived"

            for q, v in vals.items():
                quarterly.append(dict(ticker=ticker, metric=metric, fiscal_year=fyear, fq=q,
                                      end=ends[q], val=v, source=src[q]))

    cols_a = ["ticker", "metric", "fiscal_year", "start", "end", "val"]
    cols_q = ["ticker", "metric", "fiscal_year", "fq", "end", "val", "source"]
    return (pd.DataFrame(annual, columns=cols_a).drop_duplicates(["ticker", "metric", "fiscal_year"], keep="last"),
            pd.DataFrame(quarterly, columns=cols_q).drop_duplicates(["ticker", "metric", "fiscal_year", "fq"], keep="last"))


def build_clean(raw: pd.DataFrame) -> CleanData:
    selected, fallback_log = select_facts(raw)
    kind = selected["metric"].map(lambda m: TAG_MAP[m]["kind"])
    flows = selected[kind.isin(["flow", "avg"])]
    instants = selected[kind == "instant"][["ticker", "metric", "end", "val"]].reset_index(drop=True)
    annual, quarterly = derive_flows(flows)
    log.info("Clean: %d annual rows, %d quarterly rows, %d balance-sheet rows",
             len(annual), len(quarterly), len(instants))
    return CleanData(annual, quarterly, instants, fallback_log)
