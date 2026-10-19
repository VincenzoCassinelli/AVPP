"""Metric layer (pandas replacement for SQL views): EBITDA, net debt, capex, FCF, LTM, screening."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ALL_METRICS, DEFAULT_TAX_RATE, KEEP_FISCAL_YEARS
from .transform import CleanData

LTM_COLS = ["revenue", "ebitda", "net_income", "fcf", "ufcf"]


def attach_instants(periods: pd.DataFrame, instants: pd.DataFrame, end_col: str) -> pd.DataFrame:
    """Attach point-in-time balance-sheet items to each period by matching period-end dates
    (nearest within 7 days, per ticker)."""
    out = periods.copy()
    left = out[["ticker", end_col]].drop_duplicates().sort_values(end_col)
    for metric, g in instants.groupby("metric"):
        right = (g[["ticker", "end", "val"]].sort_values("end")
                 .rename(columns={"end": "_inst_end", "val": metric}))
        m = pd.merge_asof(left, right, left_on=end_col, right_on="_inst_end", by="ticker",
                          tolerance=pd.Timedelta(days=7), direction="nearest")
        out = out.merge(m[["ticker", end_col, metric]], on=["ticker", end_col], how="left")
    for metric in ALL_METRICS:
        if metric not in out:
            out[metric] = np.nan
    return out


def add_derived(df: pd.DataFrame, tax_rate: float = DEFAULT_TAX_RATE) -> pd.DataFrame:
    df = df.copy()
    df["capex"] = df["capex"].abs()                         # payments are reported positive
    df["ebitda"] = df["op_income"] + df["da"]               # EBITDA = operating income + D&A
    df["debt_total"] = df[["debt_lt", "debt_st"]].fillna(0.0).sum(axis=1)
    df["net_debt"] = df["debt_total"] - df["cash"]
    df["fcf"] = df["cfo"] - df["capex"]                     # FCF = CFO - capex
    df["ufcf"] = df["fcf"] + df["interest_expense"].fillna(0.0) * (1 - tax_rate)
    return df


def annual_metrics(clean: CleanData, tax_rate: float = DEFAULT_TAX_RATE) -> pd.DataFrame:
    a = clean.annual_flows
    wide = a.pivot_table(index=["ticker", "fiscal_year"], columns="metric", values="val", aggfunc="last")
    wide = wide.join(a.groupby(["ticker", "fiscal_year"])["end"].max().rename("fy_end")).reset_index()
    wide = attach_instants(wide, clean.instants, "fy_end")
    wide = add_derived(wide, tax_rate)
    # retain the most recent N fiscal years per ticker
    wide["_rank"] = wide.groupby("ticker")["fiscal_year"].rank(ascending=False, method="first")
    wide = wide[wide["_rank"] <= KEEP_FISCAL_YEARS].drop(columns="_rank")
    return wide.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)


def quarterly_metrics(clean: CleanData, tax_rate: float = DEFAULT_TAX_RATE) -> pd.DataFrame:
    q = clean.quarterly_flows
    if q.empty:
        return pd.DataFrame()
    keys = ["ticker", "fiscal_year", "fq"]
    wide = q.pivot_table(index=keys, columns="metric", values="val", aggfunc="last")
    wide = wide.join(q.groupby(keys)["end"].max().rename("end")).reset_index()
    wide = attach_instants(wide, clean.instants, "end")
    wide = add_derived(wide, tax_rate).sort_values(["ticker", "end"]).reset_index(drop=True)

    # trailing-twelve-month sums, only when the 4 quarters are contiguous (~9 months span)
    span = wide.groupby("ticker")["end"].diff(3).dt.days
    contiguous = span.between(250, 290)
    for c in LTM_COLS:
        roll = wide.groupby("ticker")[c].transform(lambda s: s.rolling(4, min_periods=4).sum())
        wide[f"{c}_ltm"] = roll.where(contiguous)
    maxfy = wide.groupby("ticker")["fiscal_year"].transform("max")
    return wide[wide["fiscal_year"] > maxfy - KEEP_FISCAL_YEARS].reset_index(drop=True)


def screen(annual: pd.DataFrame, quarterly: pd.DataFrame, prices: dict[str, float]) -> pd.DataFrame:
    """One row per ticker: growth, margins and (if prices are supplied) valuation multiples."""
    rows = []
    for t, a in annual.groupby("ticker"):
        a = a.sort_values("fiscal_year")
        last = a.iloc[-1]
        n = len(a)
        rev0, rev1 = a["revenue"].iloc[0], last["revenue"]
        cagr = (rev1 / rev0) ** (1 / (n - 1)) - 1 if n > 1 and rev0 and rev0 > 0 and rev1 > 0 else np.nan

        ltm = {}
        net_debt, as_of = last["net_debt"], last["fy_end"]
        if not quarterly.empty and t in set(quarterly["ticker"]):
            lq = quarterly[quarterly["ticker"] == t].sort_values("end").iloc[-1]
            if lq["end"] >= last["fy_end"]:
                ltm = {c: lq.get(f"{c}_ltm") for c in LTM_COLS}
                if pd.notna(lq["net_debt"]):
                    net_debt, as_of = lq["net_debt"], lq["end"]
        use = lambda c: ltm.get(c) if pd.notna(ltm.get(c, np.nan)) else last[c]

        rev, ebitda, fcf = use("revenue"), use("ebitda"), use("fcf")
        price = prices.get(t, np.nan)
        shares = last["shares_diluted"]
        mcap = price * shares if pd.notna(price) and pd.notna(shares) else np.nan
        ev = mcap + net_debt if pd.notna(mcap) and pd.notna(net_debt) else np.nan
        rows.append(dict(
            ticker=t, latest_fy=int(last["fiscal_year"]), balance_sheet_date=as_of,
            revenue_ltm=rev, ebitda_ltm=ebitda, fcf_ltm=fcf, revenue_cagr=cagr,
            ebitda_margin=ebitda / rev if rev else np.nan,
            fcf_margin=fcf / rev if rev else np.nan,
            net_debt=net_debt, net_debt_to_ebitda=net_debt / ebitda if ebitda and ebitda > 0 else np.nan,
            price=price, market_cap=mcap, enterprise_value=ev,
            ev_to_ebitda=ev / ebitda if pd.notna(ev) and ebitda and ebitda > 0 else np.nan,
            fcf_yield=fcf / mcap if pd.notna(mcap) and mcap > 0 else np.nan,
        ))
    out = pd.DataFrame(rows)
    if out["ev_to_ebitda"].notna().any():
        med = out["ev_to_ebitda"].median()
        out["ev_ebitda_vs_peer_median"] = out["ev_to_ebitda"] / med - 1
        out["rank_fcf_yield"] = out["fcf_yield"].rank(ascending=False, method="min")
    return out.sort_values("ticker").reset_index(drop=True)
