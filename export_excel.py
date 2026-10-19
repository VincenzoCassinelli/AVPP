"""Populate the Excel model template for each ticker and refresh the Sensitivity sheet."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

from . import dcf
from .template import FORMULA_ROWS, INPUT_ROWS, YEAR_COLS, build_template

log = logging.getLogger(__name__)

# Inputs-sheet row -> column in the annual metrics table
FIELD_FOR_ROW = {k: k for k in INPUT_ROWS if k not in FORMULA_ROWS}


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    return float(v)


def _cells(wb, name: str):
    """Resolve a workbook-level named range to its cell(s)."""
    sheet, ref = next(wb.defined_names[name].destinations)
    target = wb[sheet][ref.replace("$", "")]
    if hasattr(target, "coordinate"):          # single cell
        return [target]
    return [c for row in target for c in row]  # range, row-major


def export_ticker(ticker: str, hist: pd.DataFrame, price: float, out_dir: Path,
                  template: Path, base: dcf.Assumptions | None = None) -> Path:
    template = Path(template)
    if not template.exists():
        build_template(template)
    wb = load_workbook(template)
    hist = hist.sort_values("fiscal_year").tail(len(YEAR_COLS))
    n = len(hist)
    offset = len(YEAR_COLS) - n            # right-align so the latest year is always column F

    _cells(wb, "Ticker")[0].value = ticker
    for j, fy in enumerate(hist["fiscal_year"]):
        _cells(wb, "in_fiscal_year")[offset + j].value = int(fy)
    for key, field in FIELD_FOR_ROW.items():
        cells = _cells(wb, f"in_{key}")
        for j, v in enumerate(hist[field]):
            cells[offset + j].value = _clean(v)

    a = dcf.derive_assumptions(hist, base)
    for name, value in (("WACC", a.wacc), ("TermGrowth", a.terminal_growth), ("FadeGrowth", a.fade_growth),
                        ("StartGrowth", a.start_growth), ("FCFMargin", a.fcf_margin),
                        ("TaxRate", a.tax_rate), ("Price", price)):
        _cells(wb, name)[0].value = _clean(value)

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ticker}_model.xlsx"
    wb.save(path)
    return path


def export_all(annual: pd.DataFrame, prices: dict[str, float], out_dir: Path, template: Path) -> list[Path]:
    paths = []
    for ticker, hist in annual.groupby("ticker"):
        paths.append(export_ticker(ticker, hist, prices.get(ticker, np.nan), out_dir, template))
    log.info("Wrote %d Excel models to %s", len(paths), out_dir)
    return paths
