"""Optional share prices (needed only for market cap, EV multiples and upside)."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def load_prices(path: Path) -> dict[str, float]:
    """CSV with columns `ticker,price`. Missing file -> no prices (valuation still runs)."""
    path = Path(path)
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {str(t).upper(): float(p) for t, p in zip(df["ticker"], df["price"]) if pd.notna(p)}


def fetch_prices(tickers: list[str], path: Path) -> dict[str, float]:
    """Pull last closes with yfinance (optional dependency) and cache them to CSV."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("--fetch-prices needs yfinance:  pip install yfinance") from exc
    prices = {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(period="5d")
            prices[t] = float(hist["Close"].dropna().iloc[-1])
        except Exception as exc:
            log.warning("Could not fetch price for %s: %s", t, exc)
    pd.DataFrame({"ticker": list(prices), "price": list(prices.values())}).to_csv(path, index=False)
    return prices
