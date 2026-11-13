#!/usr/bin/env bash
# The single site check that CI and local runs share.
# 1. self-tests for the checker, 2. the checker on site/, 3. Nu Html Checker (HTML and CSS).
# Needs python3, Node.js (npx) and Java 11 or later.
set -euo pipefail

VNU_VERSION="26.10.12"

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "==> Checker self-tests"
python3 -m unittest discover -s scripts -p 'test_*.py'

echo "==> Site checks (links, third-party resources, scripts, root paths, wording)"
python3 scripts/check_site.py site

echo "==> HTML and CSS validation (Nu Html Checker, vnu-jar@${VNU_VERSION})"
npx --yes "vnu-jar@${VNU_VERSION}" --also-check-css --skip-non-html site
echo "vnu: no errors"
