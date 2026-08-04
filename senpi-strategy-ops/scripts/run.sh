#!/usr/bin/env bash
#
# run.sh — deploy a Senpi Runtime-3.0 strategy end-to-end from a one-line request.
#
#   ./run.sh <strategy-id> <branch-or-github-tree-url> <budget-usd>
#
# examples:
#   ./run.sh asia-ai https://github.com/Senpi-ai/senpi-skills/tree/main 500
#   ./run.sh asia-ai main '$500'
#
# Maps the tester request  "Run <strategy> on <url> using $<budget>"  to:
#   checkout the branch -> deploy.py create, which starts the runtime's `senpi deploy` verb
#   (preflight -> create+fund -> install -> verified tick) and polls it to a terminal report.
#
# Host prerequisites: @senpi/runtime >= 3.0.6, SENPI_AUTH_TOKEN set, and a funding
# source holding at least the strategy's min_budget (see its strategy.yaml).
set -euo pipefail

usage() { echo "usage: run.sh <strategy-id> <branch-or-github-tree-url> <budget-usd>" >&2; exit 2; }
ID="${1:-}"; SRC="${2:-}"; BUDGET="${3:-}"
[ -n "$ID" ] && [ -n "$SRC" ] && [ -n "$BUDGET" ] || usage
BUDGET="${BUDGET#\$}"                                  # tolerate a leading '$'

# branch := the /tree/<branch> segment of a GitHub url; otherwise SRC is already a branch name
BRANCH="$SRC"
case "$SRC" in *"/tree/"*) BRANCH="${SRC#*/tree/}"; BRANCH="${BRANCH%%/*}";; esac

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"            # repo root (script: senpi-strategy-ops/scripts/run.sh)
echo ">> strategy=$ID  branch=$BRANCH  budget=\$$BUDGET  repo=$ROOT"

cd "$ROOT"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

[ -d "strategies/$ID" ] || { echo "error: strategies/$ID does not exist on branch '$BRANCH'" >&2; exit 1; }

cd senpi-strategy-ops/scripts
python3 deploy.py create "$ID" --budget "$BUDGET"   # one idempotent deploy; re-run it to resume
echo ">> done — $ID deployed from '$BRANCH' with \$$BUDGET. It scans on its own cadence; flat != broken."
