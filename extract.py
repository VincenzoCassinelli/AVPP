"""SEC EDGAR extraction: ticker -> CIK resolution, companyfacts download, flattening."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests

from .config import FORMS, TAG_MAP

log = logging.getLogger(__name__)

TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
REQUEST_DELAY = 0.15  # SEC fair-access limit is 10 req/s; stay well under


class EdgarClient:
    """Thin, polite EDGAR client. The HTTP session is created lazily so fully cached
    runs work without a User-Agent."""

    def __init__(self, raw_dir: Path, tickers_cache: Path):
        self.raw_dir = Path(raw_dir)
        self.tickers_cache = Path(tickers_cache)
        self._session: requests.Session | None = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            ua = os.environ.get("SEC_USER_AGENT", "")
            if "@" not in ua:
                raise RuntimeError(
                    "SEC requires a descriptive User-Agent with contact info.\n"
                    "Set it first, e.g.:  export SEC_USER_AGENT='Jane Doe jane@example.com'")
            s = requests.Session()
            s.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
            self._session = s
        return self._session

    def _get_json(self, url: str, retries: int = 4) -> dict:
        for attempt in range(retries):
            resp = self.session.get(url, timeout=30)
            time.sleep(REQUEST_DELAY)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = 2 ** attempt
                log.warning("HTTP %s from %s; retrying in %ss", resp.status_code, url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Giving up on {url} after {retries} attempts")

    def cik_map(self, refresh: bool = False) -> dict[str, str]:
        if refresh or not self.tickers_cache.exists():
            data = self._get_json(TICKER_URL)
            self.tickers_cache.parent.mkdir(parents=True, exist_ok=True)
            self.tickers_cache.write_text(json.dumps(data))
        else:
            data = json.loads(self.tickers_cache.read_text())
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}

    def company_facts(self, ticker: str, cik: str, refresh: bool = False) -> dict:
        path = self.raw_dir / f"{ticker}.json"
        if path.exists() and not refresh:
            return json.loads(path.read_text())
        log.info("Downloading companyfacts for %s (CIK %s)", ticker, cik)
        facts = self._get_json(FACTS_URL.format(cik=cik))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(facts))
        return facts


def flatten_facts(ticker: str, facts: dict) -> pd.DataFrame:
    """Flatten one company's companyfacts JSON into one row per reported fact.

    `priority` is the index of the tag within TAG_MAP[metric]["tags"] (0 = preferred)."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    rows = []
    for metric, spec in TAG_MAP.items():
        for priority, tag in enumerate(spec["tags"]):
            node = gaap.get(tag)
            if not node:
                continue
            for it in node.get("units", {}).get(spec["unit"], []):
                if it.get("form") not in FORMS or "end" not in it:
                    continue
                rows.append({
                    "ticker": ticker, "metric": metric, "xbrl_tag": tag, "priority": priority,
                    "start": it.get("start"), "end": it["end"], "val": float(it["val"]),
                    "fy": it.get("fy"), "fp": it.get("fp"), "form": it["form"],
                    "filed": it["filed"], "accn": it.get("accn"),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        log.warning("No usable XBRL facts found for %s", ticker)
        return df
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return df


def build_raw_table(facts_by_ticker: dict[str, dict]) -> pd.DataFrame:
    frames = [flatten_facts(t, f) for t, f in facts_by_ticker.items()]
    frames = [f for f in frames if not f.empty]
    if not frames:
        raise RuntimeError("No facts extracted for any ticker")
    return pd.concat(frames, ignore_index=True)


def extract_all(tickers: list[str], client: EdgarClient, refresh: bool = False) -> dict[str, dict]:
    ciks = client.cik_map(refresh=False)
    out: dict[str, dict] = {}
    for t in tickers:
        cik = ciks.get(t.upper())
        if cik is None:
            log.error("Ticker %s not found in SEC company_tickers.json; skipping", t)
            continue
        try:
            out[t.upper()] = client.company_facts(t.upper(), cik, refresh=refresh)
        except Exception as exc:  # keep going; one bad ticker shouldn't kill the run
            log.error("Failed to extract %s: %s", t, exc)
    return out
