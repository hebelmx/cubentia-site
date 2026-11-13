#!/usr/bin/env bash
# The single site check that CI and local runs share.
# 1. self-tests for the checkers, 2. the site checker on site/, 3. the privacy-policy check
# against the app's privacy facts, 4. Nu Html Checker (HTML and CSS).
# Needs python3, Node.js (npx) and Java 11 or later.
#
# PRIVACY_FACTS is the app's docs/privacy-facts.json. CI (GITHUB_ACTIONS=true) must set it to
# an existing file. Locally it falls back to scripts/fixtures/privacy-facts.json, so local runs
# never need the network.
set -euo pipefail

VNU_VERSION="26.10.12"
FIXTURE_FACTS="scripts/fixtures/privacy-facts.json"

# A relative PRIVACY_FACTS is relative to the caller's directory, not the repo root.
if [[ -n "${PRIVACY_FACTS:-}" && "${PRIVACY_FACTS}" != /* ]]; then
  PRIVACY_FACTS="${PWD}/${PRIVACY_FACTS}"
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ -n "${PRIVACY_FACTS:-}" ]]; then
  if [[ ! -f "${PRIVACY_FACTS}" ]]; then
    echo "check.sh: PRIVACY_FACTS=${PRIVACY_FACTS} does not exist" >&2
    exit 1
  fi
elif [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  echo "check.sh: PRIVACY_FACTS must be set on CI (the app's docs/privacy-facts.json)" >&2
  exit 1
else
  PRIVACY_FACTS="${FIXTURE_FACTS}"
  echo "check.sh: PRIVACY_FACTS not set; using the local fixture ${FIXTURE_FACTS}"
fi

echo "==> Checker self-tests"
python3 -m unittest discover -s scripts -p 'test_*.py'

echo "==> Site checks (links, third-party resources, scripts, root paths, wording)"
python3 scripts/check_site.py site

echo "==> Privacy policy check (facts: ${PRIVACY_FACTS})"
python3 scripts/check_privacy.py --facts "${PRIVACY_FACTS}"

echo "==> HTML and CSS validation (Nu Html Checker, vnu-jar@${VNU_VERSION})"
npx --yes "vnu-jar@${VNU_VERSION}" --also-check-css --skip-non-html site
echo "vnu: no errors"
