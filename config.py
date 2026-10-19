"""Central configuration: peer universe, XBRL tag map, paths and constants."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Peer universe: 15 large-cap application/infrastructure software companies.
# Swap in any US-listed 10-K/10-Q filers; CIKs are resolved automatically.
# --------------------------------------------------------------------------
DEFAULT_TICKERS = [
    "MSFT", "ORCL", "ADBE", "CRM", "INTU", "NOW", "ADSK", "WDAY",
    "SNPS", "CDNS", "PANW", "FTNT", "AKAM", "PTC", "TYL",
]

FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A")
KEEP_FISCAL_YEARS = 5          # fiscal years of history retained in outputs
DEFAULT_TAX_RATE = 0.21        # used to tax-effect interest for unlevered FCF

# --------------------------------------------------------------------------
# XBRL tag map. Tags are listed in PRIORITY order: for every reporting period
# the first tag that has a value wins, so companies that changed tags over time
# (e.g. SalesRevenueNet -> RevenueFromContract...) are stitched together.
#   kind: flow    -> duration items (summed over a period; de-cumulated for quarters)
#         instant -> point-in-time balance sheet items
#         avg     -> duration items that are averages (annual only, never differenced)
# --------------------------------------------------------------------------
TAG_MAP: dict[str, dict] = {
    "revenue": dict(kind="flow", unit="USD", tags=[
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues", "SalesRevenueNet", "SalesRevenueServicesNet"]),
    "op_income": dict(kind="flow", unit="USD", tags=["OperatingIncomeLoss"]),
    "net_income": dict(kind="flow", unit="USD", tags=["NetIncomeLoss", "ProfitLoss"]),
    "da": dict(kind="flow", unit="USD", tags=[
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAmortizationAndOther"]),
    "cfo": dict(kind="flow", unit="USD", tags=[
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex": dict(kind="flow", unit="USD", tags=[
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements"]),
    "interest_expense": dict(kind="flow", unit="USD", tags=[
        "InterestExpense", "InterestExpenseDebt",
        "InterestExpenseNonoperating", "InterestAndDebtExpense"]),
    "shares_diluted": dict(kind="avg", unit="shares", tags=[
        "WeightedAverageNumberOfDilutedSharesOutstanding"]),
    "cash": dict(kind="instant", unit="USD", tags=[
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    "debt_lt": dict(kind="instant", unit="USD", tags=[
        "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
        "LongTermNotesPayable", "ConvertibleNotesPayableNoncurrent"]),
    "debt_st": dict(kind="instant", unit="USD", tags=[
        "LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]),
    "assets": dict(kind="instant", unit="USD", tags=["Assets"]),
    "liab_equity": dict(kind="instant", unit="USD", tags=["LiabilitiesAndStockholdersEquity"]),
    "equity": dict(kind="instant", unit="USD", tags=[
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
}
ALL_METRICS = list(TAG_MAP)
FLOW_METRICS = [m for m, s in TAG_MAP.items() if s["kind"] == "flow"]
CORE_METRICS = ["revenue", "op_income", "da", "cfo", "capex"]


@dataclass(frozen=True)
class Paths:
    data: Path
    raw: Path
    clean: Path
    output: Path
    models: Path
    template: Path
    prices: Path

    def ensure(self) -> "Paths":
        for p in (self.data, self.raw, self.clean, self.output, self.models):
            p.mkdir(parents=True, exist_ok=True)
        return self


def get_paths(demo: bool = False, workdir: Path | str | None = None) -> Paths:
    """Demo runs use fully separate folders so they never touch real data.

    `workdir` lets the GUI / packaged app keep everything in a user-chosen, writable folder;
    by default the project folder is used."""
    base = Path(workdir).expanduser() if workdir else ROOT
    data = base / "data" / ("demo" if demo else "")
    out = base / "output" / ("demo" if demo else "")
    return Paths(
        data=data, raw=data / "raw", clean=data / "clean", output=out,
        models=out / "models", template=base / "template.xlsx",
        prices=base / "data" / "prices.csv",
    ).ensure()
