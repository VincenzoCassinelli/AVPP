#!/usr/bin/env python3
"""Automated Valuation & Equity Screening Pipeline (command-line interface).

    python main.py --demo                 # offline run on synthetic filings (no SEC access needed)
    python main.py                        # live run: EDGAR -> clean -> metrics -> Excel models -> DCF
    python main.py --refresh              # re-download everything from EDGAR
    python main.py --tickers MSFT,ORCL    # subset of the peer universe

Prefer a window with buttons?  Run:  python gui.py

Live runs require:  export SEC_USER_AGENT='Your Name your.email@example.com'
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from valuation.pipeline import RunOptions, execute, format_report

log = logging.getLogger("pipeline")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--demo", action="store_true", help="use synthetic offline data (separate data/output folders)")
    p.add_argument("--refresh", action="store_true", help="ignore cached EDGAR downloads")
    p.add_argument("--tickers", type=lambda s: [t.strip().upper() for t in s.split(",") if t.strip()],
                   help="comma-separated tickers (default: the 15-company peer set in config.py)")
    p.add_argument("--fetch-prices", action="store_true", help="pull last close via yfinance into data/prices.csv")
    p.add_argument("--skip-excel", action="store_true", help="skip workbook generation")
    p.add_argument("--workdir", type=Path, help="folder for data/ and output/ (default: project folder)")
    p.add_argument("--strict", action="store_true", help="exit non-zero if any validation ERROR is found")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    opts = RunOptions(demo=args.demo, refresh=args.refresh, tickers=args.tickers,
                      fetch_prices=args.fetch_prices, skip_excel=args.skip_excel, workdir=args.workdir)
    try:
        result = execute(opts)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    print(format_report(result))
    if result.errors and args.strict:
        log.error("%d validation error(s); failing because --strict was set", len(result.errors))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
