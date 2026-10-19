#!/usr/bin/env python3
"""Desktop window for the Automated Valuation & Equity Screening Pipeline.

    python gui.py

No terminal needed once it is open: fill in the form, press Run, and double-click a row in the
results table to open that company's Excel model."""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pandas as pd

from valuation.config import DEFAULT_TICKERS
from valuation.pipeline import RunOptions, RunResult, execute

APP_NAME = "Equity Valuation Pipeline"
SETTINGS_FILE = Path.home() / ".equity_valuation_pipeline" / "settings.json"
DEFAULT_WORKDIR = Path.home() / "Documents" / "EquityValuationPipeline"

COLUMNS = [  # (key, heading, width, formatter)
    ("ticker", "Ticker", 70, str),
    ("value_per_share", "DCF value/share", 125, lambda v: f"${v:,.2f}"),
    ("price", "Price", 80, lambda v: f"${v:,.2f}"),
    ("upside", "Upside", 80, lambda v: f"{v:+.1%}"),
    ("revenue_cagr", "Rev. CAGR", 85, lambda v: f"{v:.1%}"),
    ("ebitda_margin", "EBITDA margin", 120, lambda v: f"{v:.1%}"),
    ("fcf_margin_ltm", "FCF margin", 90, lambda v: f"{v:.1%}"),
    ("net_debt_to_ebitda", "Net debt/EBITDA", 135, lambda v: f"{v:.2f}x"),
    ("ev_to_ebitda", "EV/EBITDA", 95, lambda v: f"{v:.1f}x"),
    ("tv_share_of_ev", "TV % of EV", 90, lambda v: f"{v:.0%}"),
]


def open_path(path: Path) -> None:
    """Open a file or folder with the operating system's default app."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class QueueHandler(logging.Handler):
    """Forwards log records to the GUI thread."""

    def __init__(self, q: queue.Queue):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self.q.put(("log", self.format(record)))


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1020x720")
        self.minsize(900, 600)
        self.q: queue.Queue = queue.Queue()
        self.result: RunResult | None = None
        self.running = False
        s = self._load_settings()

        self.var_agent = tk.StringVar(value=s.get("user_agent", ""))
        self.var_tickers = tk.StringVar(value=s.get("tickers", ", ".join(DEFAULT_TICKERS)))
        self.var_workdir = tk.StringVar(value=s.get("workdir", str(DEFAULT_WORKDIR)))
        self.var_prices = tk.StringVar(value=s.get("prices", ""))
        self.var_demo = tk.BooleanVar(value=s.get("demo", False))
        self.var_refresh = tk.BooleanVar(value=False)
        self.var_fetch = tk.BooleanVar(value=s.get("fetch_prices", False))
        self.var_skip_excel = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="Ready. Fill in the form and press Run.")

        self._build()
        self.after(100, self._poll)

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        form = ttk.LabelFrame(self, text="Settings")
        form.pack(fill="x", padx=10, pady=(10, 4))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Your name & email (SEC requires it):").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(form, textvariable=self.var_agent).grid(row=0, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(form, text="Tickers (comma-separated):").grid(row=1, column=0, sticky="w", **pad)
        self.ent_tickers = ttk.Entry(form, textvariable=self.var_tickers)
        self.ent_tickers.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)

        ttk.Label(form, text="Save results to:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(form, textvariable=self.var_workdir).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(form, text="Browse…", command=self._browse_workdir).grid(row=2, column=2, **pad)

        ttk.Label(form, text="Prices CSV (optional):").grid(row=3, column=0, sticky="w", **pad)
        ttk.Entry(form, textvariable=self.var_prices).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(form, text="Browse…", command=self._browse_prices).grid(row=3, column=2, **pad)

        opts = ttk.Frame(form)
        opts.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        for text, var in (("Demo mode (offline sample data)", self.var_demo),
                          ("Re-download from SEC", self.var_refresh),
                          ("Fetch latest prices (needs yfinance)", self.var_fetch),
                          ("Skip Excel models", self.var_skip_excel)):
            ttk.Checkbutton(opts, text=text, variable=var).pack(side="left", padx=(0, 14))
        self.var_demo.trace_add("write", lambda *_: self._sync_demo())
        self._sync_demo()

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=4)
        self.btn_run = ttk.Button(bar, text="▶  Run", command=self._on_run)
        self.btn_run.pack(side="left")
        self.btn_folder = ttk.Button(bar, text="Open results folder", command=self._open_folder, state="disabled")
        self.btn_folder.pack(side="left", padx=8)
        self.btn_model = ttk.Button(bar, text="Open selected model", command=self._open_selected, state="disabled")
        self.btn_model.pack(side="left")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="right")

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=4)
        self.tabs = tabs

        res = ttk.Frame(tabs)
        tabs.add(res, text="Results")
        self.tree = ttk.Treeview(res, columns=[c[0] for c in COLUMNS], show="headings", height=14)
        for key, head, width, _ in COLUMNS:
            self.tree.heading(key, text=head)
            self.tree.column(key, width=width, anchor="center" if key != "ticker" else "w")
        sb = ttk.Scrollbar(res, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._open_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_buttons())

        logf = ttk.Frame(tabs)
        tabs.add(logf, text="Log")
        self.log = tk.Text(logf, height=12, wrap="word", state="disabled", font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10))
        lsb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

        ttk.Label(self, textvariable=self.var_status, anchor="w").pack(fill="x", padx=12, pady=(0, 8))

    def _sync_demo(self) -> None:
        """Demo mode uses its own built-in sample companies, so the ticker box does not apply."""
        self.ent_tickers.configure(state="disabled" if self.var_demo.get() else "normal")

    # ------------------------------------------------------------ settings
    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            return {}

    def _save_settings(self) -> None:
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({
                "user_agent": self.var_agent.get().strip(), "tickers": self.var_tickers.get(),
                "workdir": self.var_workdir.get(), "prices": self.var_prices.get(),
                "demo": self.var_demo.get(), "fetch_prices": self.var_fetch.get()}))
        except OSError:
            pass  # settings are a convenience only

    # ------------------------------------------------------------- actions
    def _browse_workdir(self) -> None:
        d = filedialog.askdirectory(title="Choose where to save results", initialdir=self.var_workdir.get() or str(Path.home()))
        if d:
            self.var_workdir.set(d)

    def _browse_prices(self) -> None:
        f = filedialog.askopenfilename(title="Choose prices CSV (columns: ticker, price)",
                                       filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if f:
            self.var_prices.set(f)

    def _on_run(self) -> None:
        if self.running:
            return
        agent = self.var_agent.get().strip()
        if not self.var_demo.get() and "@" not in agent:
            messagebox.showwarning(APP_NAME, "SEC requires your name and email, for example:\n\n"
                                             "Jane Smith jane.smith@gmail.com\n\n"
                                             "(or tick Demo mode to try the app offline).")
            return
        tickers = [t.strip().upper() for t in self.var_tickers.get().replace("\n", ",").split(",") if t.strip()]
        opts = RunOptions(
            demo=self.var_demo.get(), refresh=self.var_refresh.get(),
            tickers=None if self.var_demo.get() else (tickers or None),
            fetch_prices=self.var_fetch.get(), skip_excel=self.var_skip_excel.get(),
            workdir=Path(self.var_workdir.get()).expanduser(), user_agent=agent or None,
            prices_path=Path(self.var_prices.get()).expanduser() if self.var_prices.get().strip() else None)
        self._save_settings()
        self.running = True
        self.btn_run.configure(state="disabled")
        self.progress.start(12)
        self.tree.delete(*self.tree.get_children())
        self._append_log(f"--- Run started ({'demo' if opts.demo else 'live SEC data'}) ---")
        self.var_status.set("Running… this can take a minute or two for live data.")
        self.tabs.select(1)
        threading.Thread(target=self._worker, args=(opts,), daemon=True).start()

    def _worker(self, opts: RunOptions) -> None:
        handler = QueueHandler(self.q)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        try:
            self.q.put(("done", execute(opts)))
        except Exception as exc:  # surfaced to the user in a dialog
            logging.getLogger("pipeline").exception("Run failed")
            self.q.put(("error", str(exc) or exc.__class__.__name__))
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._fail(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _end_run(self) -> None:
        self.running = False
        self.progress.stop()
        self.btn_run.configure(state="normal")

    def _fail(self, message: str) -> None:
        self._end_run()
        self.var_status.set("Run failed. See the Log tab for details.")
        messagebox.showerror(APP_NAME, message)

    def _finish(self, res: RunResult) -> None:
        self._end_run()
        self.result = res
        table = res.combined_table()
        for row in table.itertuples(index=False):
            d = row._asdict()
            values = []
            for key, _, _, fmt in COLUMNS:
                v = d.get(key)
                values.append("–" if v is None or (not isinstance(v, str) and pd.isna(v)) else fmt(v))
            self.tree.insert("", "end", iid=d["ticker"], values=values)
        n_err, n_warn = len(res.errors), len(res.issues) - len(res.errors)
        self.var_status.set(f"Done: {len(table)} companies, {len(res.model_paths)} Excel models. "
                            f"{n_err} validation errors, {n_warn} warnings. Double-click a row to open its model.")
        self.tabs.select(0)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.btn_folder.configure(state="normal" if self.result else "disabled")
        has_model = bool(self.result and self.tree.selection() and self._model_path(self.tree.selection()[0]))
        self.btn_model.configure(state="normal" if has_model else "disabled")

    def _model_path(self, ticker: str) -> Path | None:
        if not self.result:
            return None
        p = self.result.models_dir / f"{ticker}_model.xlsx"
        return p if p.exists() else None

    def _open_folder(self) -> None:
        if self.result:
            open_path(self.result.output_dir)

    def _open_selected(self) -> None:
        sel = self.tree.selection()
        path = self._model_path(sel[0]) if sel else None
        if path:
            open_path(path)
        elif sel:
            messagebox.showinfo(APP_NAME, "No Excel model for that ticker (was 'Skip Excel models' ticked?).")


def selftest() -> int:
    """Headless check used to verify a packaged build: run the offline demo end to end."""
    import tempfile
    res = execute(RunOptions(demo=True, workdir=Path(tempfile.mkdtemp())))
    ok = len(res.model_paths) == 6 and not res.errors
    print("SELFTEST", "OK" if ok else "FAILED", f"({len(res.model_paths)} models)")
    return 0 if ok else 1


def main() -> None:
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    App().mainloop()


if __name__ == "__main__":
    main()
