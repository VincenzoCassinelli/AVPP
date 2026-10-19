"""Five-year DCF and WACC x terminal-growth sensitivity grid (pure Python / NumPy)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import DEFAULT_TAX_RATE

PROJECTION_YEARS = 5
WACC_GRID = np.round(np.arange(0.07, 0.11 + 1e-9, 0.005), 4)   # 7.0% .. 11.0%
G_GRID = np.round(np.arange(0.010, 0.030 + 1e-9, 0.005), 4)    # 1.0% .. 3.0%


@dataclass
class Assumptions:
    wacc: float = 0.09
    terminal_growth: float = 0.025
    fade_growth: float = 0.03        # revenue growth reached in the final projection year
    start_growth: float = 0.08       # year-1 revenue growth (default: clipped historical CAGR)
    fcf_margin: float = float("nan")  # unlevered FCF / revenue (default: 3-yr historical mean)
    tax_rate: float = DEFAULT_TAX_RATE


def derive_assumptions(hist: pd.DataFrame, base: Assumptions | None = None) -> Assumptions:
    """Data-driven starting point: revenue CAGR (clipped 0-20%) and mean unlevered FCF margin."""
    a = Assumptions() if base is None else Assumptions(**base.__dict__)
    h = hist.sort_values("fiscal_year")
    rev = h["revenue"].dropna()
    if len(rev) >= 2 and rev.iloc[0] > 0 and rev.iloc[-1] > 0:
        cagr = (rev.iloc[-1] / rev.iloc[0]) ** (1 / (len(rev) - 1)) - 1
        a.start_growth = float(np.clip(cagr, 0.0, 0.20))
    m = (h["ufcf"] / h["revenue"]).replace([np.inf, -np.inf], np.nan).dropna().tail(3)
    a.fcf_margin = float(m.mean()) if len(m) else float("nan")
    return a


def project_fcf(last_revenue: float, a: Assumptions, years: int = PROJECTION_YEARS) -> list[float]:
    """Revenue growth fades linearly from start_growth (yr 1) to fade_growth (final yr);
    FCF = revenue x constant margin."""
    fcfs, rev = [], last_revenue
    for t in range(1, years + 1):
        g = a.start_growth + (a.fade_growth - a.start_growth) * (t - 1) / (years - 1)
        rev *= 1 + g
        fcfs.append(rev * a.fcf_margin)
    return fcfs


def enterprise_value(fcfs: list[float], wacc: float, g: float) -> tuple[float, float]:
    """Returns (EV, PV of terminal value). End-of-year discounting, Gordon-growth terminal value."""
    if wacc <= g:
        return float("nan"), float("nan")
    n = len(fcfs)
    pv = sum(f / (1 + wacc) ** t for t, f in enumerate(fcfs, 1))
    pv_tv = fcfs[-1] * (1 + g) / (wacc - g) / (1 + wacc) ** n
    return pv + pv_tv, pv_tv


def value_per_share(fcfs, wacc, g, net_debt, shares) -> float:
    if not shares or shares <= 0 or np.isnan(shares) or np.isnan(net_debt):
        return float("nan")
    ev, _ = enterprise_value(fcfs, wacc, g)
    return (ev - net_debt) / shares


def sensitivity_grid(fcfs, net_debt, shares, waccs=WACC_GRID, gs=G_GRID) -> pd.DataFrame:
    data = [[value_per_share(fcfs, w, g, net_debt, shares) for g in gs] for w in waccs]
    return pd.DataFrame(data, index=[f"{w:.1%}" for w in waccs], columns=[f"{g:.1%}" for g in gs])


def run_dcf(hist: pd.DataFrame, price: float = float("nan"), base: Assumptions | None = None) -> dict:
    """Base-case valuation summary for one ticker (used for the summary CSV and test cross-checks)."""
    a = derive_assumptions(hist, base)
    last = hist.sort_values("fiscal_year").iloc[-1]
    res = dict(ticker=last["ticker"], wacc=a.wacc, terminal_growth=a.terminal_growth,
               start_growth=a.start_growth, fcf_margin=a.fcf_margin,
               value_per_share=np.nan, price=price, upside=np.nan, tv_share_of_ev=np.nan)
    if np.isnan(a.fcf_margin) or pd.isna(last["revenue"]):
        return res
    fcfs = project_fcf(last["revenue"], a)
    ev, pv_tv = enterprise_value(fcfs, a.wacc, a.terminal_growth)
    vps = value_per_share(fcfs, a.wacc, a.terminal_growth, last["net_debt"], last["shares_diluted"])
    res.update(value_per_share=vps, tv_share_of_ev=pv_tv / ev if ev else np.nan,
               upside=vps / price - 1 if price and not np.isnan(price) and price > 0 else np.nan)
    return res
