# Automated Valuation & Equity Screening Pipeline

[![CI](https://github.com/<your-username>/equity-valuation-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/<your-username>/equity-valuation-pipeline/actions)

A Python pipeline that pulls five years of 10-K / 10-Q data for a peer group of 15 equities from the
**SEC EDGAR XBRL API**, cleans it, computes operating metrics with **pandas** (no database required),
auto-populates a live **Excel 3-statement + DCF model** for every company, and generates
**WACC × terminal-growth sensitivity tables**.

```
SEC EDGAR API ─► extract.py ─► transform.py ─► metrics.py ─► export_excel.py ─► Excel models
 (companyfacts)   flatten      dedupe, tag      EBITDA, net     fills template     IS / BS / CF /
                  to table     priority,        debt, capex,    via named ranges   DCF / Sensitivity
                               de-cumulate,     FCF, LTM,
                               derive Q4        screening ─► dcf.py (base case + grid)
```

## Quick start

```bash
git clone https://github.com/<your-username>/equity-valuation-pipeline.git
cd equity-valuation-pipeline
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1) Try it offline first (synthetic filings in EDGAR's exact JSON shape)
python main.py --demo

# 2) Live run against SEC EDGAR (SEC requires you to identify yourself)
export SEC_USER_AGENT="Your Name your.email@example.com"
python main.py
```

| Flag | Purpose |
|---|---|
| `--demo` | Offline run on synthetic data; writes to `data/demo` and `output/demo` |
| `--refresh` | Re-download EDGAR data instead of using the local cache |
| `--tickers MSFT,ORCL` | Run a subset (default peer set is in `valuation/config.py`) |
| `--fetch-prices` | Pull last closes via `yfinance` (`pip install yfinance`) into `data/prices.csv` |
| `--skip-excel` | Skip workbook generation |
| `--strict` | Exit non-zero on any validation error (used in CI) |

Prices are optional. Without them you still get all fundamentals and the DCF per-share value;
supply `data/prices.csv` (`ticker,price`) to also get market cap, EV/EBITDA, FCF yield and upside.

## Use it as a desktop app (no terminal)

A simple window with a form, a Run button, a live log and a results table. Double-click a row to open
that company's Excel model.

**Easiest (macOS):** double-click **`Run GUI.command`**. The first launch sets itself up (1–2 minutes,
needs internet); after that it opens the window straight away. If macOS blocks it the first time,
right-click the file → **Open** → **Open**.

**Or from a terminal:** `python gui.py`

**Or build a real double-clickable app** (macOS `.app`):

```bash
./build_app.sh        # output: dist/Equity Valuation Pipeline.app  -> drag it to Applications
```

In the window: enter your name and email (SEC requires it), choose where to save results
(default `~/Documents/EquityValuationPipeline`), and press **Run**. Tick **Demo mode** to try it offline.
Your settings are remembered in `~/.equity_valuation_pipeline/settings.json`.

Notes: the macOS build must be done on a Mac, and an app you build yourself opens normally; if you send
it to someone else, Gatekeeper will ask them to right-click → Open because it isn't Apple-notarized.
Python from **python.org** includes Tk (needed for the window); Homebrew Python needs `brew install python-tk`.

## Outputs (`output/`)

| File | Contents |
|---|---|
| `models/<TICKER>_model.xlsx` | Inputs, IS, BS, CF, DCF, Sensitivity (live formulas) |
| `annual_metrics.csv` / `quarterly_metrics.csv` | Cleaned metrics incl. LTM columns |
| `screen.csv` | Peer screen: growth, margins, leverage, EV/EBITDA, FCF yield |
| `valuation_summary.csv` | Base-case DCF value per share, terminal-value share of EV |
| `fallback_tag_log.csv` | Every place a non-preferred XBRL tag supplied data (audit trail) |

## How the data cleaning works

EDGAR's `companyfacts` endpoint returns every fact a company ever filed. Three things make it unusable
as-is, and `valuation/transform.py` handles each:

1. **Duplicates / restatements** – each period reappears as a prior-year comparative in later filings.
   For each tag and period the most recently filed value wins.
2. **Tag drift** – companies change XBRL tags over time. `TAG_MAP` lists tags in priority order and the
   best available tag is chosen *per period*, so series stay continuous. Non-preferred picks are logged.
3. **Cumulative cash flows and missing Q4** – 10-Q cash-flow items are year-to-date and no Q4 filing exists.
   Discrete quarters are `Q1 = YTD3`, `Q2 = YTD6 − YTD3`, `Q3 = YTD9 − YTD6`, `Q4 = FY − YTD9`.
   Standalone 3M income-statement values are used directly when reported.

### Definitions

- **EBITDA** = operating income + depreciation & amortization (not a GAAP tag)
- **Net debt** = long-term debt + current debt − cash & equivalents (leases excluded; missing debt tags are treated as 0 and flagged)
- **FCF** = cash from operations − capex
- **Unlevered FCF** = FCF + interest expense × (1 − 21%), used for the DCF
- **Fiscal year** = calendar year of the fiscal-year-end date

## DCF and sensitivity

Five-year projection: revenue growth fades linearly from the historical CAGR (clipped to 0–20%) to a
3% target; unlevered FCF = revenue × trailing 3-year mean FCF margin; Gordon-growth terminal value,
end-of-year discounting. Defaults are WACC 9.0% and terminal growth 2.5%; every assumption is an editable
blue cell on the `Inputs` sheet. The `Sensitivity` sheet is a 9 × 5 grid (WACC 7–11% × g 1–3%) built from
explicit formulas, so it recalculates when anything changes, with green/red conditional formatting versus
the current price.

**Limitations to keep in mind:** terminal value is ~75% of enterprise value in the demo (typical for DCFs),
a constant-margin projection is a simplification, WACC is an input rather than estimated from CAPM, and
diluted weighted-average shares are used rather than period-end shares outstanding.
Some filers use tags outside `TAG_MAP`; the validation warnings tell you which metrics are missing so you
can add tags in `valuation/config.py`.

## Testing

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests compare the cleaned output against the synthetic generator's known ground truth (quarter recovery,
restatement handling, tag stitching), check the DCF math, and — if LibreOffice is installed — recalculate the
generated workbook headlessly and assert that Excel's DCF and every cell of the sensitivity grid match the
Python implementation.

## Project layout

```
main.py                  command-line entry point
gui.py                   desktop window (Tkinter)
Run GUI.command          macOS double-click launcher
build_app.sh             builds a standalone app with PyInstaller
template.xlsx            generated Excel template (python -m valuation.template to rebuild)
valuation/
  config.py              peer tickers, XBRL tag map, paths
  extract.py             EDGAR client (User-Agent, rate limit, retries, cache) + flattening
  transform.py           dedupe, tag priority, de-cumulation, Q4 derivation
  metrics.py             EBITDA / net debt / capex / FCF, LTM, screening
  dcf.py                 projection, valuation, sensitivity grid
  template.py            builds the Excel template (named ranges, formulas)
  export_excel.py        writes data into the template for each ticker
  pipeline.py            orchestration shared by CLI and GUI
  validate.py            data-quality checks
  prices.py              optional price loading
  demo_data.py           synthetic EDGAR-shaped data for offline runs and tests
tests/
```

## Disclaimer

For educational and research purposes only; not investment advice. Data is from SEC EDGAR; verify figures
against the original filings before relying on them.
