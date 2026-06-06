#!/usr/bin/env bash
# Build the canvas-dl CLI and GUI standalone binaries locally with PyInstaller.
# Usage: ./scripts/build_binaries.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"

echo ">> Installing build dependencies"
"$PYTHON" -m pip install -e ".[build]"

echo ">> Building CLI (one-file)"
"$PYTHON" -m PyInstaller --noconfirm --clean canvas-dl-cli.spec

echo ">> Building GUI (one-dir)"
"$PYTHON" -m PyInstaller --noconfirm --clean canvas-dl-gui.spec

echo ">> Done. Artifacts in ./dist:"
ls -la dist
