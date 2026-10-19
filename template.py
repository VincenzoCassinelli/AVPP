"""Builds template.xlsx: Inputs / IS / BS / CF / DCF / Sensitivity.

Everything downstream of `Inputs` is a live Excel formula, so writing data into Inputs
(see export_excel.py) is all that is needed to refresh a model. Cells are addressed through
workbook-level named ranges (e.g. `in_revenue`, `WACC`), not hardcoded coordinates."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName

from .dcf import G_GRID, WACC_GRID

YEAR_COLS = ["B", "C", "D", "E", "F"]           # 5 fiscal years, latest in F
MM = '#,##0.0,,;(#,##0.0,,);"-"'                  # raw dollars displayed in $ millions
PCT = "0.0%"
USD = '$#,##0.00'

# Inputs sheet rows (single source of truth for layout)
INPUT_ROWS = {
    "revenue": 5, "op_income": 6, "da": 7, "ebitda": 8, "net_income": 9, "cfo": 10,
    "capex": 11, "fcf": 12, "interest_expense": 13, "ufcf": 14, "cash": 15,
    "debt_total": 16, "net_debt": 17, "shares_diluted": 18, "assets": 19,
    "liab_equity": 20, "equity": 21,
}
INPUT_LABELS = {
    "revenue": "Revenue", "op_income": "Operating income", "da": "Depreciation & amortization",
    "ebitda": "EBITDA (op. income + D&A)", "net_income": "Net income", "cfo": "Cash from operations",
    "capex": "Capital expenditures", "fcf": "Free cash flow (CFO - capex)",
    "interest_expense": "Interest expense", "ufcf": "Unlevered FCF (FCF + after-tax interest)",
    "cash": "Cash & equivalents", "debt_total": "Total debt", "net_debt": "Net debt",
    "shares_diluted": "Diluted shares (millions)", "assets": "Total assets",
    "liab_equity": "Total liabilities & equity", "equity": "Stockholders' equity",
}
# rows that are formulas in the template (the exporter must not overwrite these)
FORMULA_ROWS = {"ebitda", "fcf", "ufcf", "net_debt"}

ASSUMPTION_CELLS = {  # named range -> (row, label, number format)
    "WACC": (25, "WACC", PCT),
    "TermGrowth": (26, "Terminal growth rate", PCT),
    "FadeGrowth": (27, "Year-5 revenue growth (fade target)", PCT),
    "StartGrowth": (28, "Year-1 revenue growth", PCT),
    "FCFMargin": (29, "Unlevered FCF margin", PCT),
    "TaxRate": (30, "Tax rate on interest", PCT),
    "Price": (31, "Current share price ($)", USD),
}

HEADER_FILL = PatternFill("solid", start_color="1F3864")
INPUT_FONT = Font(color="0000FF")                 # blue = hardcoded input (banking convention)
BOLD = Font(bold=True)
THIN = Side(style="thin", color="999999")


def _name(wb: Workbook, name: str, ref: str) -> None:
    wb.defined_names[name] = DefinedName(name, attr_text=ref)


def _header(ws, row: int, labels: list[str]) -> None:
    for i, text in enumerate(labels):
        c = ws.cell(row=row, column=1 + i, value=text)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center" if i else "left")


def _year_header(ws, row: int = 3) -> None:
    _header(ws, row, ["USD millions unless noted"] + [None] * 5)
    for col in YEAR_COLS:
        ws[f"{col}{row}"] = f'=IF(Inputs!{col}4="","","FY"&Inputs!{col}4)'


def _line(ws, row: int, label: str, fmt: str, formula, bold: bool = False, first_col_blank: bool = False):
    ws.cell(row=row, column=1, value=label).font = Font(bold=bold)
    for i, col in enumerate(YEAR_COLS):
        if first_col_blank and i == 0:
            continue
        prev = YEAR_COLS[i - 1] if i else None
        c = ws[f"{col}{row}"]
        c.value = formula(col, prev)
        c.number_format = fmt
        c.font = Font(bold=bold)


def build_template(path: Path | str) -> Path:
    wb = Workbook()

    # ------------------------------------------------------------------ Inputs
    ws = wb.active
    ws.title = "Inputs"
    ws["A1"] = "Inputs (written by the pipeline; blue = hardcoded)"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = "Ticker"
    ws["B2"].font = INPUT_FONT
    _header(ws, 4, ["Fiscal year"] + [None] * 5)
    for r_key, row in INPUT_ROWS.items():
        ws.cell(row=row, column=1, value=INPUT_LABELS[r_key])
        for col in YEAR_COLS:
            c = ws[f"{col}{row}"]
            c.number_format = MM
            if r_key not in FORMULA_ROWS:
                c.font = INPUT_FONT
        _name(wb, f"in_{r_key}", f"Inputs!$B${row}:$F${row}")
    _name(wb, "in_fiscal_year", "Inputs!$B$4:$F$4")
    _name(wb, "Ticker", "Inputs!$B$2")

    R = INPUT_ROWS
    for col in YEAR_COLS:
        ws[f"{col}{R['ebitda']}"] = f"=IF(COUNT({col}{R['op_income']},{col}{R['da']})=2,{col}{R['op_income']}+{col}{R['da']},\"\")"
        ws[f"{col}{R['fcf']}"] = f"=IF(COUNT({col}{R['cfo']},{col}{R['capex']})=2,{col}{R['cfo']}-{col}{R['capex']},\"\")"
        ws[f"{col}{R['ufcf']}"] = (f"=IF(COUNT({col}{R['cfo']},{col}{R['capex']})=2,"
                                   f"{col}{R['cfo']}-{col}{R['capex']}+N({col}{R['interest_expense']})*(1-TaxRate),\"\")")
        ws[f"{col}{R['net_debt']}"] = f"=IF(COUNT({col}{R['cash']})=1,{col}{R['debt_total']}-{col}{R['cash']},\"\")"

    ws["A24"] = "DCF assumptions (defaults derived from history; edit freely)"
    ws["A24"].font = BOLD
    for name, (row, label, fmt) in ASSUMPTION_CELLS.items():
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2)
        c.number_format, c.font = fmt, INPUT_FONT
        _name(wb, name, f"Inputs!$B${row}")
    ws.column_dimensions["A"].width = 44
    for col in YEAR_COLS:
        ws.column_dimensions[col].width = 14

    # ---------------------------------------------------------------------- IS
    is_ = wb.create_sheet("IS")
    is_["A1"] = "Income statement summary"
    is_["A1"].font = Font(bold=True, size=13)
    _year_header(is_)
    ref = lambda key: (lambda col, prev: f"=Inputs!{col}{R[key]}")
    _line(is_, 4, "Revenue", MM, ref("revenue"), bold=True)
    _line(is_, 5, "  growth %", PCT, lambda c, p: f'=IFERROR({c}4/{p}4-1,"")', first_col_blank=True)
    _line(is_, 6, "Operating income", MM, ref("op_income"))
    _line(is_, 7, "  operating margin", PCT, lambda c, p: f'=IFERROR({c}6/{c}4,"")')
    _line(is_, 8, "D&A", MM, ref("da"))
    _line(is_, 9, "EBITDA", MM, ref("ebitda"), bold=True)
    _line(is_, 10, "  EBITDA margin", PCT, lambda c, p: f'=IFERROR({c}9/{c}4,"")')
    _line(is_, 11, "Net income", MM, ref("net_income"))
    _line(is_, 12, "  net margin", PCT, lambda c, p: f'=IFERROR({c}11/{c}4,"")')

    # ---------------------------------------------------------------------- BS
    bs = wb.create_sheet("BS")
    bs["A1"] = "Balance sheet summary"
    bs["A1"].font = Font(bold=True, size=13)
    _year_header(bs)
    _line(bs, 4, "Cash & equivalents", MM, ref("cash"))
    _line(bs, 5, "Total assets", MM, ref("assets"), bold=True)
    _line(bs, 6, "Total liabilities", MM,
          lambda c, p: f'=IF(COUNT(Inputs!{c}{R["liab_equity"]},Inputs!{c}{R["equity"]})=2,Inputs!{c}{R["liab_equity"]}-Inputs!{c}{R["equity"]},"")')
    _line(bs, 7, "Stockholders' equity", MM, ref("equity"))
    _line(bs, 8, "Total liabilities & equity", MM, ref("liab_equity"), bold=True)
    _line(bs, 9, "Balance check (assets - L&E)", MM,
          lambda c, p: f'=IF(COUNT({c}5,{c}8)=2,{c}5-{c}8,"")')
    _line(bs, 10, "Total debt", MM, ref("debt_total"))
    _line(bs, 11, "Net debt", MM, ref("net_debt"), bold=True)
    _line(bs, 12, "Net debt / EBITDA (x)", '0.0"x"', lambda c, p: f'=IFERROR(IF(IS!{c}9>0,{c}11/IS!{c}9,""),"")')

    # ---------------------------------------------------------------------- CF
    cf = wb.create_sheet("CF")
    cf["A1"] = "Cash flow summary"
    cf["A1"].font = Font(bold=True, size=13)
    _year_header(cf)
    _line(cf, 4, "Cash from operations", MM, ref("cfo"))
    _line(cf, 5, "Capital expenditures", MM, ref("capex"))
    _line(cf, 6, "Free cash flow", MM, ref("fcf"), bold=True)
    _line(cf, 7, "  FCF margin", PCT, lambda c, p: f'=IFERROR({c}6/IS!{c}4,"")')
    _line(cf, 8, "  FCF conversion (FCF / net income)", PCT, lambda c, p: f'=IFERROR({c}6/IS!{c}11,"")')
    _line(cf, 9, "Interest expense", MM, ref("interest_expense"))
    _line(cf, 10, "Unlevered FCF", MM, ref("ufcf"), bold=True)
    for sh in (is_, bs, cf):
        sh.column_dimensions["A"].width = 38
        for col in YEAR_COLS:
            sh.column_dimensions[col].width = 14

    # --------------------------------------------------------------------- DCF
    d = wb.create_sheet("DCF")
    d["A1"] = "Five-year DCF (live formulas; driven by named assumptions on Inputs)"
    d["A1"].font = Font(bold=True, size=13)
    _header(d, 2, ["USD millions unless noted", "Year 1", "Year 2", "Year 3", "Year 4", "Year 5"])
    d["A3"] = "Projection year"
    for i, col in enumerate(YEAR_COLS, 1):
        d[f"{col}3"] = i
    d["A4"] = "Revenue growth"
    d["A5"] = "Revenue"
    d["A6"] = "Unlevered FCF margin"
    d["A7"] = "Unlevered FCF"
    d["A8"] = "Discount factor"
    d["A9"] = "PV of FCF"
    for i, col in enumerate(YEAR_COLS):
        prev = YEAR_COLS[i - 1] if i else None
        d[f"{col}4"] = f"=StartGrowth+(FadeGrowth-StartGrowth)*({col}3-1)/4"
        d[f"{col}5"] = f"=Inputs!F{R['revenue']}*(1+{col}4)" if i == 0 else f"={prev}5*(1+{col}4)"
        d[f"{col}6"] = "=FCFMargin"
        d[f"{col}7"] = f"={col}5*{col}6"
        d[f"{col}8"] = f"=1/(1+WACC)^{col}3"
        d[f"{col}9"] = f"={col}7*{col}8"
        for r, f in ((4, PCT), (5, MM), (6, PCT), (7, MM), (8, "0.0000"), (9, MM)):
            d[f"{col}{r}"].number_format = f
    summary = [
        (11, "Sum of PV of FCF", "=SUM(B9:F9)", MM),
        (12, "Terminal value (Gordon growth)", "=F7*(1+TermGrowth)/(WACC-TermGrowth)", MM),
        (13, "PV of terminal value", "=B12*F8", MM),
        (14, "Enterprise value", "=B11+B13", MM),
        (15, "Less: net debt (latest FY)", f"=N(Inputs!F{R['net_debt']})", MM),
        (16, "Equity value", "=B14-B15", MM),
        (17, "Diluted shares (millions)", f"=N(Inputs!F{R['shares_diluted']})", MM),
        (18, "Equity value per share ($)",
         '=IF(AND(B17>0,ISNUMBER(FCFMargin),WACC>TermGrowth),B16/B17,"n/a")', USD),
        (19, "Current price ($)", "=Price", USD),
        (20, "Upside / (downside)", '=IF(AND(ISNUMBER(B18),N(Price)>0),B18/Price-1,"n/a")', PCT),
        (21, "Terminal value as % of EV", '=IFERROR(B13/B14,"n/a")', PCT),
    ]
    for row, label, formula, fmt in summary:
        d[f"A{row}"] = label
        d[f"B{row}"] = formula
        d[f"B{row}"].number_format = fmt
    for row in (14, 18):
        d[f"A{row}"].font = d[f"B{row}"].font = BOLD
    d.column_dimensions["A"].width = 38
    for col in YEAR_COLS:
        d.column_dimensions[col].width = 14

    # ------------------------------------------------------------- Sensitivity
    s = wb.create_sheet("Sensitivity")
    s["A1"] = "Equity value per share ($): WACC (rows) x terminal growth (columns)"
    s["A1"].font = Font(bold=True, size=13)
    s["A2"], s["B2"] = "Current price", "=Price"
    s["B2"].number_format = USD
    s["A3"] = "Green = at/above current price; red = below. Headers are editable inputs."
    s["A4"] = "WACC \\ g"
    s["A4"].font = BOLD
    for j, g in enumerate(G_GRID):
        c = s.cell(row=4, column=2 + j, value=float(g))
        c.number_format, c.font = PCT, Font(bold=True, color="0000FF")
    first_row = 5
    for i, w in enumerate(WACC_GRID):
        r = first_row + i
        c = s.cell(row=r, column=1, value=float(w))
        c.number_format, c.font = PCT, Font(bold=True, color="0000FF")
        for j in range(len(G_GRID)):
            col = get_column_letter(2 + j)
            s[f"{col}{r}"] = (
                f'=IF(AND(N(Inputs!$F${R["shares_diluted"]})>0,ISNUMBER(FCFMargin),$A{r}>{col}$4),'
                f"(SUMPRODUCT(DCF!$B$7:$F$7/(1+$A{r})^DCF!$B$3:$F$3)"
                f"+DCF!$F$7*(1+{col}$4)/($A{r}-{col}$4)/(1+$A{r})^DCF!$F$3"
                f'-DCF!$B$15)/DCF!$B$17,"n/a")')
            s[f"{col}{r}"].number_format = USD
    last_row = first_row + len(WACC_GRID) - 1
    last_col = get_column_letter(1 + len(G_GRID))
    rng = f"B{first_row}:{last_col}{last_row}"
    s.conditional_formatting.add(rng, FormulaRule(
        formula=[f"AND(ISNUMBER(B{first_row}),N($B$2)>0,B{first_row}>=$B$2)"],
        fill=PatternFill("solid", start_color="C6EFCE", end_color="C6EFCE")))
    s.conditional_formatting.add(rng, FormulaRule(
        formula=[f"AND(ISNUMBER(B{first_row}),N($B$2)>0,B{first_row}<$B$2)"],
        fill=PatternFill("solid", start_color="FFC7CE", end_color="FFC7CE")))
    s.column_dimensions["A"].width = 14
    for j in range(len(G_GRID)):
        s.column_dimensions[get_column_letter(2 + j)].width = 12
    _name(wb, "Sens_Grid", f"Sensitivity!$B${first_row}:${last_col}${last_row}")

    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = None
    path = Path(path)
    wb.save(path)
    return path


if __name__ == "__main__":
    print(build_template(Path(__file__).resolve().parents[1] / "template.xlsx"))
