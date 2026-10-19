#!/bin/bash
# Double-click this file in Finder to open the app window (macOS).
# First launch creates a private Python environment and installs the packages (needs internet, ~1-2 min).
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3)
if [ -z "$PY" ]; then
  echo "Python 3 is not installed. Download it from https://www.python.org/downloads/ and try again."
  read -n 1 -s -r -p "Press any key to close..."; exit 1
fi

if [ ! -d .venv ]; then
  echo "First-time setup: creating environment..."
  "$PY" -m venv .venv || { echo "Could not create the environment."; read -n 1 -s -r -p "Press any key to close..."; exit 1; }
  source .venv/bin/activate
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt || { echo "Package install failed."; read -n 1 -s -r -p "Press any key to close..."; exit 1; }
else
  source .venv/bin/activate
fi

if ! python -c "import tkinter" 2>/dev/null; then
  echo "This Python has no Tk support (common with Homebrew Python)."
  echo "Install Python from https://www.python.org/downloads/macos/ (it includes Tk), delete the .venv folder, and try again."
  read -n 1 -s -r -p "Press any key to close..."; exit 1
fi

python gui.py
