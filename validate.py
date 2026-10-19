"""Data-quality checks. Errors fail a --strict run; warnings are logged."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import CORE_METRICS, KEEP_FISCAL_YEARS


@dataclass
class Issue:
    level: str      # "ERROR" | "WARN"
    ticker: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.ticker}: {self.message}"


def validate(annual: pd.DataFrame, quarterly: pd.DataFrame, expected: list[str],
             tol: float = 0.005) -> list[Issue]:
    issues: list[Issue] = []
    present = set(annual["ticker"])
    for t in expected:
        if t not in present:
            issues.append(Issue("ERROR", t, "no annual data extracted"))

    for t, a in annual.groupby("ticker"):
        if len(a) < KEEP_FISCAL_YEARS:
            issues.append(Issue("WARN", t, f"only {len(a)} fiscal years available (wanted {KEEP_FISCAL_YEARS})"))
        for m in CORE_METRICS:
            if a[m].isna().all():
                issues.append(Issue("WARN", t, f"metric '{m}' missing for all years (check tag map / fallback log)"))
            elif a[m].isna().any():
                issues.append(Issue("WARN", t, f"metric '{m}' missing in some years"))
        if (a["revenue"] < 0).any():
            issues.append(Issue("ERROR", t, "negative revenue"))
        if a[["debt_lt", "debt_st"]].isna().all().all():
            issues.append(Issue("WARN", t, "no debt tags found; total debt assumed to be 0"))

        bal = a.dropna(subset=["assets", "liab_equity"])
        bad = bal[(bal["assets"] - bal["liab_equity"]).abs() > tol * bal["assets"].abs()]
        for r in bad.itertuples():
            issues.append(Issue("ERROR", t, f"FY{r.fiscal_year} balance sheet does not balance "
                                            f"(assets {r.assets:,.0f} vs L&E {r.liab_equity:,.0f})"))

    # quarters must add back to the reported fiscal year
    if not quarterly.empty:
        for metric in ("revenue", "cfo"):
            qs = (quarterly.groupby(["ticker", "fiscal_year"])[metric]
                  .agg(["sum", "count"]).reset_index())
            merged = qs.merge(annual[["ticker", "fiscal_year", metric]], on=["ticker", "fiscal_year"])
            merged = merged[merged["count"] == 4]
            diff = (merged["sum"] - merged[metric]).abs() / merged[metric].abs().replace(0, np.nan)
            for r in merged[diff > tol].itertuples():
                issues.append(Issue("ERROR", r.ticker,
                                    f"FY{r.fiscal_year} quarters of '{metric}' do not sum to the annual figure"))
    return issues
