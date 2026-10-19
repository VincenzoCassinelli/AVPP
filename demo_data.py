"""Synthetic SEC `companyfacts`-shaped data so the pipeline can run offline (``--demo``).

The generator reproduces the quirks the cleaning layer exists to handle:
  * 10-Q cash-flow items are year-to-date; income-statement items also carry standalone 3M values
  * there is no Q4 filing
  * every period reappears as a prior-year comparative in later filings; original filings carry
    slightly different (pre-restatement) numbers that the later comparatives overwrite
  * one company switches revenue tag (SalesRevenueNet -> RevenueFromContract...)
  * one company reports no debt; one omits interest expense
  * fiscal year-ends differ (Dec / Sep / Jun / Jan)
All names are fictional.
"""
from __future__ import annotations

import calendar
import random
from datetime import date, timedelta

DEMO = {  # ticker: (cik, name, fye_month, seed)
    "DMOA": (9000001, "Demo Alpha Software", 12, 1),
    "DMOB": (9000002, "Demo Bravo Systems", 9, 2),
    "DMOC": (9000003, "Demo Charlie Cloud", 6, 3),
    "DMOD": (9000004, "Demo Delta Data", 1, 4),
    "DMOE": (9000005, "Demo Echo Analytics", 12, 5),
    "DMOF": (9000006, "Demo Foxtrot Security", 9, 6),
}
FIRST_FY, LAST_FY = 2020, 2025
SEASON = [0.23, 0.24, 0.25, 0.28]     # share of annual flow earned in each quarter
RATIOS = {  # metric -> annual ratio to revenue
    "op_income": 0.26, "net_income": 0.21, "da": 0.04, "cfo": 0.32,
    "capex": 0.05, "interest_expense": 0.008,
}
IS_ITEMS = {"revenue", "op_income", "net_income", "interest_expense"}   # carry standalone 3M values
TAGS = {
    "revenue_new": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "revenue_old": "SalesRevenueNet",
    "op_income": "OperatingIncomeLoss", "net_income": "NetIncomeLoss",
    "da": "DepreciationDepletionAndAmortization",
    "cfo": "NetCashProvidedByUsedInOperatingActivities",
    "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
    "interest_expense": "InterestExpense",
    "shares": "WeightedAverageNumberOfDilutedSharesOutstanding",
    "cash": "CashAndCashEquivalentsAtCarryingValue",
    "debt_lt": "LongTermDebtNoncurrent", "debt_st": "LongTermDebtCurrent",
    "assets": "Assets", "liab_equity": "LiabilitiesAndStockholdersEquity",
    "equity": "StockholdersEquity",
}


def _month_end(y: int, m: int) -> date:
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])


class _Company:
    def __init__(self, idx: int, ticker: str, fye_month: int, seed: int):
        rng = random.Random(seed)
        self.idx, self.fye_month = idx, fye_month
        self.rev0 = rng.uniform(3e9, 18e9)
        self.growth = rng.uniform(0.05, 0.14)
        self.noise = {(fy, q, m): 1 + rng.uniform(-0.02, 0.02)
                      for fy in range(FIRST_FY, LAST_FY + 1) for q in range(4) for m in ["revenue", *RATIOS]}
        self.orig_bias = {fy: (0.0 if fy == LAST_FY else rng.uniform(-0.015, 0.015))
                          for fy in range(FIRST_FY, LAST_FY + 1)}
        self.assets0 = self.rev0 * rng.uniform(1.5, 3.0)
        self.debt0 = 0.0 if idx == 2 else self.rev0 * rng.uniform(0.2, 0.6)
        self.shares0 = rng.uniform(300e6, 900e6)

    # --- calendar helpers -------------------------------------------------
    def fy_end(self, fy: int) -> date:
        return _month_end(fy, self.fye_month)

    def fy_start(self, fy: int) -> date:
        return self.fy_end(fy - 1) + timedelta(days=1)

    def q_end(self, fy: int, q: int) -> date:       # q = 1..4
        return self.fy_end(fy) if q == 4 else _month_end(self.fy_end(fy - 1).year, self.fye_month + 3 * q)

    # --- "true" values ----------------------------------------------------
    def quarter_value(self, metric: str, fy: int, q: int) -> float:      # q = 0..3
        annual_rev = self.rev0 * (1 + self.growth) ** (fy - FIRST_FY)
        base = annual_rev * (1.0 if metric == "revenue" else RATIOS[metric])
        return base * SEASON[q] * self.noise[(fy, q, metric)]

    def instant(self, metric: str, d: date) -> float:
        t = (d.year - FIRST_FY) + d.month / 12
        assets = self.assets0 * (1 + 0.08) ** t
        return {"assets": assets, "liab_equity": assets, "equity": assets * 0.6,
                "cash": assets * 0.15, "debt_lt": self.debt0 * (1.03) ** t,
                "debt_st": self.debt0 * 0.1}[metric]

    def shares(self, fy: int) -> float:
        return self.shares0 * (0.99 ** (fy - FIRST_FY))


def _rev_tag(c: _Company, filing_fy: int) -> str:
    return TAGS["revenue_old"] if (c.idx == 0 and filing_fy <= 2021) else TAGS["revenue_new"]


def _company_facts(c: _Company, name: str, cik: int) -> dict:
    gaap: dict[str, dict] = {}
    n = [0]

    def add(tag, unit, **item):
        gaap.setdefault(tag, {"label": tag, "units": {}})["units"].setdefault(unit, []).append(item)

    def iso(d):
        return d.isoformat()

    for fy in range(FIRST_FY, LAST_FY + 1):
        for fp, qn in (("Q1", 1), ("Q2", 2), ("Q3", 3), ("FY", 4)):
            form = "10-K" if fp == "FY" else "10-Q"
            period_end = c.q_end(fy, qn)
            filed = period_end + timedelta(days=60 if fp == "FY" else 40)
            n[0] += 1
            accn = f"{cik:010d}-{fy % 100:02d}-{n[0]:06d}"
            meta = dict(accn=accn, fy=fy, fp=fp, form=form, filed=iso(filed))

            # comparative sets: (reporting_fy, bias) -> current year then prior year
            for rfy, bias_fn in ((fy, lambda m: 1 + c.orig_bias[fy]), (fy - 1, lambda m: 1.0)):
                if rfy < FIRST_FY:
                    continue
                start = c.fy_start(rfy)
                for metric in ["revenue", *RATIOS]:
                    if metric == "interest_expense" and c.idx == 3:
                        continue
                    tag = _rev_tag(c, fy) if metric == "revenue" else TAGS[metric]
                    qvals = [c.quarter_value(metric, rfy, q) * bias_fn(metric) for q in range(4)]
                    cum = [sum(qvals[: q + 1]) for q in range(4)]
                    # year-to-date value ending at this filing's period
                    add(tag, "USD", start=iso(start), end=iso(c.q_end(rfy, qn)), val=round(cum[qn - 1]), **meta)
                    # standalone quarter (income-statement items, 10-Qs only)
                    if metric in IS_ITEMS and fp != "FY" and qn > 1:
                        add(tag, "USD", start=iso(c.q_end(rfy, qn - 1) + timedelta(days=1)),
                            end=iso(c.q_end(rfy, qn)), val=round(qvals[qn - 1]), **meta)
                add(TAGS["shares"], "shares", start=iso(start), end=iso(c.q_end(rfy, qn)),
                    val=round(c.shares(rfy)), **meta)
                # balance sheet at this period end (current-year column only)
                if rfy == fy:
                    for metric in ("cash", "debt_lt", "debt_st", "assets", "liab_equity", "equity"):
                        if metric.startswith("debt") and c.idx == 2:
                            continue
                        add(TAGS[metric], "USD", end=iso(period_end),
                            val=round(c.instant(metric, period_end)), **meta)
    # keep only filings that already exist as of the dataset end
    return {"cik": cik, "entityName": name, "facts": {"us-gaap": gaap}}


def generate_demo_facts() -> dict[str, dict]:
    return {t: _company_facts(_Company(i, t, m, seed), name, cik)
            for i, (t, (cik, name, m, seed)) in enumerate(DEMO.items())}
