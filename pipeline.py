"""End-to-end orchestration shared by the command line (main.py) and the desktop app (gui.py)."""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import dcf, demo_data, export_excel, extract, metrics, transform, validate
from . import prices as price_mod
from .config import DEFAULT_TAX_RATE, DEFAULT_TICKERS, get_paths
from .template import build_template

log = logging.getLogger("pipeline")


@dataclass
class RunOptions:
    demo: bool = False
    refresh: bool = False
    tickers: list[str] | None = None
    fetch_prices: bool = False
    skip_excel: bool = False
    workdir: Path | None = None          # where data/ and output/ live (default: project folder)
    user_agent: str | None = None        # "Name email@example.com"; else $SEC_USER_AGENT is used
    prices_path: Path | None = None      # optional CSV (ticker,price) to use instead of data/prices.csv


@dataclass
class RunResult:
    annual: pd.DataFrame
    quarterly: pd.DataFrame
    screen: pd.DataFrame
    summary: pd.DataFrame
    issues: list[validate.Issue]
    output_dir: Path
    models_dir: Path
    model_paths: list[Path] = field(default_factory=list)

    @property
    def errors(self) -> list[validate.Issue]:
        return [i for i in self.issues if i.level == "ERROR"]

    def combined_table(self) -> pd.DataFrame:
        """Screen + DCF summary joined on ticker (what the GUI shows)."""
        keep = ["ticker", "revenue_cagr", "ebitda_margin", "fcf_margin", "net_debt_to_ebitda", "ev_to_ebitda"]
        s = self.screen[[c for c in keep if c in self.screen]].rename(columns={"fcf_margin": "fcf_margin_ltm"})
        d = self.summary[["ticker", "value_per_share", "price", "upside", "tv_share_of_ev"]]
        return d.merge(s, on="ticker", how="left")


def execute(opts: RunOptions) -> RunResult:
    paths = get_paths(demo=opts.demo, workdir=opts.workdir)
    if opts.user_agent:
        os.environ["SEC_USER_AGENT"] = opts.user_agent.strip()

    # ---- 1. extract -------------------------------------------------------
    log.info("Step 1/6: extracting filings")
    if opts.demo:
        facts = demo_data.generate_demo_facts()
        tickers = opts.tickers or list(facts)
        facts = {t: facts[t] for t in tickers if t in facts}
        if not facts:
            raise RuntimeError("Demo mode only knows tickers: " + ", ".join(demo_data.DEMO))
        log.info("Demo mode: %d synthetic companies", len(facts))
    else:
        tickers = opts.tickers or DEFAULT_TICKERS
        client = extract.EdgarClient(paths.raw, paths.data / "company_tickers.json")
        try:
            facts = extract.extract_all(tickers, client, refresh=opts.refresh)
        except requests.RequestException as exc:
            raise RuntimeError(
                "Could not reach SEC EDGAR. Check your internet connection (or VPN / network "
                f"restrictions) and that your name and email are valid.\n\nDetails: {exc.__class__.__name__}") from exc
        if not facts:
            raise RuntimeError("No company data could be downloaded. Check the tickers and your internet connection.")
    raw = extract.build_raw_table(facts)
    raw.to_parquet(paths.raw / "raw_facts.parquet")
    log.info("Raw facts: %s rows across %d tickers", f"{len(raw):,}", raw["ticker"].nunique())

    # ---- 2. clean ---------------------------------------------------------
    log.info("Step 2/6: cleaning (restatements, tag priority, quarters)")
    clean = transform.build_clean(raw)
    clean.annual_flows.to_parquet(paths.clean / "annual_flows.parquet")
    clean.quarterly_flows.to_parquet(paths.clean / "quarterly_flows.parquet")
    clean.instants.to_parquet(paths.clean / "instants.parquet")
    clean.fallback_log.to_csv(paths.output / "fallback_tag_log.csv", index=False)
    if not clean.fallback_log.empty:
        log.info("Fallback XBRL tags used for %d ticker/metric pairs (see fallback_tag_log.csv)",
                 len(clean.fallback_log))

    # ---- 3. metrics -------------------------------------------------------
    log.info("Step 3/6: computing EBITDA, net debt, capex, FCF")
    annual = metrics.annual_metrics(clean, DEFAULT_TAX_RATE)
    quarterly = metrics.quarterly_metrics(clean, DEFAULT_TAX_RATE)
    annual.to_csv(paths.output / "annual_metrics.csv", index=False)
    quarterly.to_csv(paths.output / "quarterly_metrics.csv", index=False)

    # ---- 4. validate ------------------------------------------------------
    log.info("Step 4/6: validating")
    issues = validate.validate(annual, quarterly, list(facts))
    for i in issues:
        (log.error if i.level == "ERROR" else log.warning)("%s", i)

    # ---- 5. prices, screen, DCF summary ----------------------------------
    log.info("Step 5/6: screening and DCF")
    prices: dict[str, float] = {}
    if not opts.demo:
        if opts.prices_path:
            prices_file = Path(opts.prices_path)
            if prices_file.resolve() != paths.prices.resolve():
                shutil.copyfile(prices_file, paths.prices)
        if opts.fetch_prices:
            prices = price_mod.fetch_prices(sorted(set(annual["ticker"])), paths.prices)
        else:
            prices = price_mod.load_prices(paths.prices)
    screen = metrics.screen(annual, quarterly, prices)
    screen.to_csv(paths.output / "screen.csv", index=False)
    summary = pd.DataFrame([dcf.run_dcf(h, prices.get(t, np.nan)) for t, h in annual.groupby("ticker")])
    summary.to_csv(paths.output / "valuation_summary.csv", index=False)

    # ---- 6. Excel models --------------------------------------------------
    model_paths: list[Path] = []
    if not opts.skip_excel:
        log.info("Step 6/6: writing Excel models")
        if not paths.template.exists():
            build_template(paths.template)
        model_paths = export_excel.export_all(annual, prices, paths.models, paths.template)
    log.info("Done. Results saved to %s", paths.output)
    return RunResult(annual, quarterly, screen, summary, issues, paths.output, paths.models, model_paths)


def format_report(res: RunResult) -> str:
    show = res.screen[["ticker", "latest_fy", "revenue_ltm", "ebitda_margin", "fcf_margin",
                       "revenue_cagr", "net_debt_to_ebitda"]].copy()
    show["revenue_ltm"] = (show["revenue_ltm"] / 1e9).round(2)
    dcf_tbl = res.summary[["ticker", "start_growth", "fcf_margin", "value_per_share", "tv_share_of_ev"]]
    return ("\nPEER SCREEN (revenue in $bn)\n" + show.round(3).to_string(index=False)
            + "\n\nBASE-CASE DCF\n" + dcf_tbl.round(3).to_string(index=False)
            + f"\n\nOutputs written to {res.output_dir}")
