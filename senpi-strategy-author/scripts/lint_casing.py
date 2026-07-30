#!/usr/bin/env python3
"""Casing guard — stop NEW coin-symbol upper-casing from landing in strategy scanners.

WHY THIS EXISTS
---------------
Hyperliquid coin names are CASE-SENSITIVE. The 1000x-denominated names carry a
lowercase `k` (kPEPE / kSHIB / kBONK) and `KPEPE` is rejected as
`INVALID_ARGUMENT`; HIP-3 assets carry a lowercase `xyz:` prefix (xyz:GOLD,
xyz:BRENTOIL). A scanner that upper-cases a symbol and then EMITS it (as the
signal asset) or PASSES it to a Senpi tool (market_get_asset_data,
create_position, ...) silently no-trades every affected name. This has been the
source of repeated silent-failure bugs (PR #501 fixed 5 strategies; the follow-up
sweep fixed 5 more).

THE RULE
--------
Preserve a symbol's case AT THE SOURCE. Upper-case ONLY at a comparison / dict-key
site, right where the comparison happens:

  BAD  (source-side upper):   coin = str(pos.get("coin","")).upper()
  BAD  (symbol helper):       def position_asset(p): return str(...).upper()
  GOOD (compare at the site): sm_map.get(coin.upper())
  GOOD (compare at the site): if sig["asset"].upper() in held_upper: ...

RATCHET MODEL (important)
-------------------------
A regex cannot tell an EMITTED upper-cased symbol (bug) from an upper-cased
COMPARISON KEY (fine) — both look like `x = y.upper()`. So this guard does NOT try
to classify the ~100 pre-existing sites. Instead it works as a ratchet:

  * `--baseline <file>` grandfathers the sites present when the baseline was cut
    (a tracked audit backlog — see the casing second-wave audit). The guard FAILS
    only on a symbol upper-casing that is NOT in the baseline — i.e. NEW code.
  * `# casing-ok: <reason>` on a line always exempts it (use when you've verified
    the upper-cased value never reaches an emit or a tool call).
  * `--write-baseline <file>` regenerates the baseline from the current tree.

Result: new strategies / edits cannot reintroduce the bug, while the legacy
backlog is worked down separately by the token/tok audit.

USAGE
-----
    python3 lint_casing.py [--baseline FILE] [ROOT]      # check (CI)
    python3 lint_casing.py --write-baseline FILE [ROOT]  # regenerate backlog
    python3 lint_casing.py --report [ROOT]               # list ALL sites, exit 0
"""
import os
import re
import sys
import glob

SYMBOL_TOKENS = ("coin", "asset", "sym", "symbol", "ticker", "market",
                 "instrument", "token", "tok", "name")
ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*.*\.upper\(\)')
RETURN_RE = re.compile(r'^\s*return\s+.*\.upper\(\)')
OK_MARK = "casing-ok"


def _is_symbol_var(name):
    n = name.lower()
    return any(tok in n for tok in SYMBOL_TOKENS)


def _key(relpath, stripped):
    # line-number-independent identity so the baseline survives edits elsewhere
    return f"{relpath}\t{stripped}"


def scan_tree(root):
    """Return {key: (relpath, lineno, stripped, kind)} for all source-side symbol uppers."""
    strat_glob = os.path.join(root, "strategies", "*", "*", "scanners", "*.py")
    files = sorted(f for f in glob.glob(strat_glob) if "/tests/" not in f)
    hits = {}
    for f in files:
        rel = os.path.relpath(f, root)
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh, 1):
                line = raw.rstrip("\n")
                if OK_MARK in line or ".upper()" not in line:
                    continue
                m = ASSIGN_RE.match(line)
                kind = None
                if m and _is_symbol_var(m.group(1)):
                    kind = "assign"
                elif RETURN_RE.match(line):
                    kind = "return"
                if kind:
                    stripped = line.strip()
                    hits[_key(rel, stripped)] = (rel, i, stripped, kind)
    return hits, len(files)


def load_baseline(path):
    if not path or not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as fh:
        return {ln.rstrip("\n") for ln in fh if ln.strip() and not ln.startswith("#")}


def main(argv):
    args = [a for a in argv[1:]]
    baseline_path = None
    write_baseline = None
    report_only = False
    root = "."
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--baseline":
            i += 1; baseline_path = args[i]
        elif a == "--write-baseline":
            i += 1; write_baseline = args[i]
        elif a == "--report":
            report_only = True
        else:
            root = a
        i += 1

    hits, nfiles = scan_tree(root)

    if write_baseline:
        with open(write_baseline, "w", encoding="utf-8") as fh:
            fh.write("# casing-guard baseline — pre-existing symbol upper-casing "
                     "sites (audit backlog, NOT certified safe).\n")
            fh.write("# Regenerate: python3 lint_casing.py --write-baseline "
                     f"{os.path.basename(write_baseline)} .\n")
            for k in sorted(hits):
                fh.write(k + "\n")
        print(f"casing-guard: wrote {len(hits)} baseline entries to {write_baseline} "
              f"(scanned {nfiles} files).")
        return 0

    if report_only:
        for k in sorted(hits):
            rel, ln, text, kind = hits[k]
            print(f"  {rel}:{ln}  [{kind}]  {text}")
        print(f"\n{len(hits)} site(s) across "
              f"{len(set(v[0] for v in hits.values()))} file(s). (report only)")
        return 0

    baseline = load_baseline(baseline_path)
    new = {k: v for k, v in hits.items() if k not in baseline}

    if not new:
        print(f"casing-guard: OK — scanned {nfiles} files; "
              f"{len(hits)} known site(s) grandfathered, 0 new.")
        return 0

    print("casing-guard: FAIL — NEW source-side coin-symbol upper-casing.\n")
    print("HL coin names are case-sensitive (kPEPE/kSHIB/kBONK, xyz:). Preserve")
    print("case at the source; upper-case only at the comparison site. If this")
    print("line is genuinely safe, annotate it `# casing-ok: <reason>`.\n")
    for k in sorted(new):
        rel, ln, text, kind = new[k]
        print(f"  {rel}:{ln}  [{kind}]  {text}")
    print(f"\n{len(new)} new violation(s). (If intentional + safe, add "
          f"`# casing-ok`; do NOT add to the baseline to bypass review.)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
