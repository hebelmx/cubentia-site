#!/usr/bin/env bash
# Serve site/ at http://localhost:PORT/cubentia-site/ so links resolve exactly as on GitHub Pages.
# Usage: scripts/preview.sh [PORT]   (default 8000). Ctrl+C stops it and removes the preview directory.
# python3 -m http.server does not serve 404.html for missing paths; open /cubentia-site/404.html to see it.
set -euo pipefail

PORT="${1:-8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREVIEW_DIR="$(mktemp -d "${ROOT}/.preview.XXXXXX")"
trap 'rm -rf "${PREVIEW_DIR}"' EXIT
ln -s "${ROOT}/site" "${PREVIEW_DIR}/cubentia-site"

echo "Serving http://localhost:${PORT}/cubentia-site/"
python3 -m http.server "${PORT}" --bind 127.0.0.1 --directory "${PREVIEW_DIR}"
