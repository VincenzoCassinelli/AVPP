#!/bin/bash
# Builds a standalone double-clickable app with PyInstaller.
#   macOS  -> dist/Equity Valuation Pipeline.app
#   Linux  -> dist/Equity Valuation Pipeline/   (folder with executable)
# Run from the project folder:   ./build_app.sh
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements-build.txt

rm -rf build dist
pyinstaller --noconfirm --clean --windowed --name "Equity Valuation Pipeline" \
  --collect-submodules valuation \
  --collect-all pyarrow \
  --hidden-import openpyxl.cell._writer \
  gui.py

echo
echo "Build finished. Your app is in the 'dist' folder."
if [ "$(uname)" = "Darwin" ]; then echo "Drag 'dist/Equity Valuation Pipeline.app' into your Applications folder."; fi
