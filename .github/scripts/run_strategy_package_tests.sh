#!/usr/bin/env bash
# Run every strategies/<id>/tests suite — 37 directories that CI never ran before.
#
# They cannot be collected in one pytest session: many share a basename (test_scan.py,
# test_scoring.py) with no __init__.py, and each puts its own scanners/ dir on sys.path, so
# `pytest strategies/*/tests` stops with an INTERNALERROR during collection. One process per
# directory avoids both.
#
# Two styles exist, and each is gated on its own exit code:
#   pytest-style  — a file defining `def test_` or `class Test`; run with pytest, per directory.
#   script-style  — checks at module level ending in `sys.exit(1 if FAIL else 0)` (the chimp
#                   family). pytest cannot import these (the exit aborts collection), so each
#                   runs as `python3 <file>`; its nonzero exit on a failed check is the gate.
set -u

# Every pytest-style directory would fail on a missing pytest and read as a wall of test failures.
if ! python3 -c "import pytest" 2>/dev/null; then
  echo "pytest is not importable by $(command -v python3) — install it first (pip install pytest)" >&2
  exit 2
fi

rc=0; dirs=0; failed=()

for d in strategies/*/tests; do
  [ -d "$d" ] || continue
  dirs=$((dirs + 1))
  pyfiles=(); scripts=()
  for f in "$d"/test_*.py; do
    [ -f "$f" ] || continue
    if grep -qE '^[[:space:]]*def test_|^class Test' "$f"; then pyfiles+=("$f"); else scripts+=("$f"); fi
  done

  if [ ${#pyfiles[@]} -gt 0 ]; then
    echo "::group::pytest $d"
    python3 -m pytest -q -p no:cacheprovider "${pyfiles[@]}"
    status=$?
    echo "::endgroup::"
    if [ $status -ne 0 ]; then rc=1; failed+=("$d (pytest exit $status)"); fi
  fi

  for f in ${scripts[@]+"${scripts[@]}"}; do
    echo "::group::script $f"
    python3 "$f"
    status=$?
    echo "::endgroup::"
    if [ $status -ne 0 ]; then rc=1; failed+=("$f (exit $status)"); fi
  done
done

echo "strategy package suites: $dirs directories"
if [ $rc -ne 0 ]; then
  printf 'FAILED:\n'; printf '  %s\n' "${failed[@]}"
fi
exit $rc
