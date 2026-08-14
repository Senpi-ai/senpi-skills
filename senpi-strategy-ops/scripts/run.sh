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
# Exit: 0 when the deploy went through — live (0), installed-unobserved (4) or still running (6),
# each reported in its own words. Anything else (1 could-not-answer / 2 refused / 3 failed /
# 5 interrupted) aborts carrying the deploy's own code.
#
# Host prerequisites: an @senpi-ai/runtime carrying the `senpi deploy` verb, SENPI_AUTH_TOKEN set, and a funding
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
# Deploy the package we JUST checked out, BY PATH. A bare id would resolve through the durable
# strategies root (a stale copy from an earlier run) or a remote fetch of the default ref — so the
# tester would fund main's, or last week's, package while believing they tested "$BRANCH".
#
# Capture the deploy's D-12 code instead of letting `set -e` abort on it: 4 (installed, first tick
# not observed yet — routine on a long interval_seconds) and 6 (job still running at the budget
# lapse) both mean the deploy went through, and aborting on them swallowed the report, so a working
# branch read as a broken one.
RC=0
python3 deploy.py create "$ROOT/strategies/$ID" --budget "$BUDGET" || RC=$?   # idempotent; re-run to resume

WHERE="from $ROOT/strategies/$ID (branch '$BRANCH') with \$$BUDGET"
case "$RC" in
  0) echo ">> done — $ID deployed $WHERE, and a scanner tick was observed. It scans on its own cadence; flat != broken." ;;
  4) echo ">> done — $ID deployed $WHERE, but its FIRST SCANNER TICK was not observed inside the wait."
     echo ">> That is expected on a long interval_seconds — not a failure, and not yet proof of life."
     echo ">> Check in a few minutes:  openclaw senpi scanner -r <runtime_id>   (ids: openclaw senpi runtime list)" ;;
  6) echo ">> $ID: the deploy job is STILL RUNNING (a wallet may still be funding) — nothing failed."
     echo ">> Watch it:  openclaw senpi deploy status      (re-running this script resumes it, it never duplicates)" ;;
  *) echo "error: the deploy did not complete (exit $RC) — read the report above, it carries the cause and the next step." >&2
     echo "       The job's own record:  openclaw senpi deploy status" >&2
     exit "$RC" ;;
esac
