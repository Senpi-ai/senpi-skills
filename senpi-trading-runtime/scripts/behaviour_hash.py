#!/usr/bin/env python3
"""A package's BEHAVIOUR hash — what the runtime actually executes, ignoring prose.

Why this exists. `deploy.py` refreshes a package on disk only when the fetched version differs
from the local one (`deploy.py`: `if loadable and new_v is not None and new_v == old_v`), and on a
match it DELETES the fetched copy. So a behavioural change shipped WITHOUT a `strategy.yaml`
version bump never reaches a box that already holds that version — it fetches the fixed copy,
compares the version, throws the fix away, and logs "already current".

That is not hypothetical. Three behavioural fixes shipped that way:

  * #825, 2026-10-02 — penguin's 3% pre-move gate. Jason's live penguin refetched 1.1.0 at
    00:07:41 UTC; the gate merged at 21:33:12 UTC the same day, still as 1.1.0. Four days later a
    fresh $3,820 deploy was still running the ungated scanner.
  * 5ed84ae1, 2026-10-01 — "the fraction guard is strictly below 1 — 1% no longer sizes at 100%",
    across 35 `scan.py` files. A sizing bug.
  * 99760337, 2026-09-28 — "never upper-case the asset symbol that reaches an order".

The naive guard — "any change under main/ requires a version bump" — is unusable: it fires on
comment and prose edits, which are most commits in this tree, and a guard that cries wolf gets
bumped past. So this compares what the runtime READS:

  * YAML: `yaml.safe_load`, then a canonical JSON dump. Comments, key order and quoting style are
    invisible; a changed threshold is not.
  * Python: TEXT with comment-only lines, trailing whitespace and blank lines removed. Not a
    parser — see `_canon_py` for why `ast.dump` and `tokenize` both failed here.
  * Anything else (JSON fixtures, data files): raw bytes.

The hash must mean the same thing on every interpreter this repo touches — operator boxes, CI's
3.11, the runtime image. The first attempt used `ast.dump`, whose output is version-dependent, and
every package mismatched under CI. Portability is a correctness property of this file, not a nicety.

`.senpi-proof.json`, `.deploy-state.json` and `__pycache__` are excluded — deploy-time artefacts,
not authored behaviour.

Usage:
    python3 senpi-trading-runtime/scripts/behaviour_hash.py                 # print pkg -> hash
    python3 senpi-trading-runtime/scripts/behaviour_hash.py --write         # regenerate the lock
    python3 senpi-trading-runtime/scripts/behaviour_hash.py --pkg penguin   # one package
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import hashlib
import json
import os

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STRATEGIES = os.path.join(REPO_ROOT, "strategies")
LOCK_PATH = os.path.join(STRATEGIES, "version_lock.json")

# Deploy-time artefacts and caches: present on a deployed box, never authored, never behaviour.
SKIP_NAMES = {".senpi-proof.json", ".deploy-state.json", ".DS_Store"}
SKIP_DIRS = {"__pycache__", "tests", ".git"}


def _canon_py(src):
    """Normalise Python TEXTUALLY — deliberately not with a parser.

    The first version of this used `ast.dump`, and it was wrong in a way that made the whole guard
    useless: `ast.dump`'s output is not stable across Python versions, so a lock generated on 3.14
    mismatched every package under CI's 3.11. `tokenize` is no better — 3.12 split f-strings into
    FSTRING_START/MIDDLE/END, so the token stream for ordinary scanner code changes across that
    boundary too.

    These lines run on operator boxes, CI, and whatever the runtime image ships. A hash that is
    only valid on one interpreter cannot gate anything, so the canonical form is pure text:

      * drop lines whose first non-space character is `#`  (comment blocks — the bulk of the prose
        in this tree, and the thing that would otherwise make this guard fire on every doc commit)
      * strip trailing whitespace, drop blank lines

    What that does NOT ignore, stated plainly rather than discovered later: a trailing inline
    comment (`x = 3  # why`) and a reworded docstring both move the hash, so both need a version
    bump. That is the cost of a guard that behaves identically everywhere, and the bump is harmless
    — it delivers identical behaviour to boxes that were already current.
    """
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        line = line.rstrip()
        if line:
            out.append(line)
    return "\n".join(out).encode()


def _canon(path):
    """The behaviour-bearing content of one file, as bytes."""
    name = os.path.basename(path)
    if name.endswith((".yaml", ".yml")):
        with open(path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        # Parsed, so comments, key order and quoting style are all invisible — and `json.dumps`
        # with sorted keys is byte-identical on every Python 3.
        return json.dumps(doc, sort_keys=True, default=str, separators=(",", ":")).encode()
    if name.endswith(".py"):
        with open(path, encoding="utf-8") as fh:
            return _canon_py(fh.read())
    with open(path, "rb") as fh:
        return fh.read()


def _instance_dirs(pkg_dir):
    """Every directory the manifest declares a runtime in, plus the manifest itself.

    Read from `strategy.yaml` rather than globbed, so a stray backup directory inside a package
    cannot change the hash and a package that stops shipping an instance does.
    """
    man_path = os.path.join(pkg_dir, "strategy.yaml")
    with open(man_path, encoding="utf-8") as fh:
        man = yaml.safe_load(fh) or {}
    rel = {"strategy.yaml"}
    for inst in (man.get("instances") or []):
        runtime = inst.get("runtime")
        if runtime:
            rel.add(runtime)
            d = os.path.dirname(runtime)
            scanners = os.path.join(pkg_dir, d, "scanners")
            if os.path.isdir(scanners):
                for f in sorted(os.listdir(scanners)):
                    if f in SKIP_NAMES or f in SKIP_DIRS:
                        continue
                    if os.path.isfile(os.path.join(scanners, f)):
                        rel.add(os.path.join(d, "scanners", f))
    return man, sorted(rel)


def package_hash(pkg_dir):
    """(version, hash, [files]) for one package directory."""
    man, rel_files = _instance_dirs(pkg_dir)
    h = hashlib.sha256()
    counted = []
    for rel in rel_files:
        path = os.path.join(pkg_dir, rel)
        if not os.path.isfile(path):
            continue
        if os.path.basename(path) in SKIP_NAMES:
            continue
        h.update(rel.encode())
        h.update(b"\0")
        h.update(_canon(path))
        h.update(b"\0")
        counted.append(rel)
    return str(man.get("version") or ""), h.hexdigest()[:16], counted


def all_packages():
    out = {}
    for sid in sorted(os.listdir(STRATEGIES)):
        pkg_dir = os.path.join(STRATEGIES, sid)
        if not os.path.isfile(os.path.join(pkg_dir, "strategy.yaml")):
            continue
        version, digest, files = package_hash(pkg_dir)
        out[sid] = {"version": version, "behaviour": digest, "files": len(files)}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="regenerate strategies/version_lock.json")
    ap.add_argument("--allow-stale-version", action="store_true",
                    help="write even where behaviour changed without a version bump — for the "
                         "INITIAL lock of pre-existing drift only, never for a new change")
    ap.add_argument("--pkg", help="print one package's hash and the files that fed it")
    a = ap.parse_args()

    if a.pkg:
        version, digest, files = package_hash(os.path.join(STRATEGIES, a.pkg))
        print(f"{a.pkg}  version={version}  behaviour={digest}")
        for f in files:
            print(f"    {f}")
        return 0

    pkgs = all_packages()
    if a.write:
        # THE ENFORCEMENT LIVES HERE, not only in the test. If `--write` simply recorded whatever
        # it found, an author who changed behaviour could regenerate the lock, keep the same
        # version, and CI would go green — reintroducing the exact bug this guards. So compare
        # against the committed lock and REFUSE when a package's behaviour moved while its version
        # stood still. The author then bumps the version, which is the thing that makes deploy.py
        # refresh the package on every box that already holds it.
        if not a.allow_stale_version and os.path.isfile(LOCK_PATH):
            with open(LOCK_PATH, encoding="utf-8") as fh:
                prev = (json.load(fh) or {}).get("packages") or {}
            stuck = []
            for sid, now in sorted(pkgs.items()):
                was = prev.get(sid)
                if not was:
                    continue                      # a new package: nothing to propagate past
                if was.get("behaviour") != now["behaviour"] and was.get("version") == now["version"]:
                    stuck.append((sid, now["version"]))
            if stuck:
                lines = "\n".join(f"    {sid}: behaviour changed, still version {v}"
                                  for sid, v in stuck)
                raise SystemExit(
                    "error: refusing to write the lock — these packages changed behaviour without a "
                    f"version bump:\n{lines}\n\n"
                    "  deploy.py refreshes a package on VERSION INEQUALITY only, and on a match it\n"
                    "  DELETES the copy it just fetched. So this change will never reach a box that\n"
                    "  already holds that version: it will fetch the fix, compare the version, throw\n"
                    "  the fix away, and log \"already current\".\n\n"
                    "  Bump `version:` in each strategy.yaml above, then run this again.\n"
                    "  (--allow-stale-version exists only for the initial lock of pre-existing drift.)")
        doc = {
            "_instructions": (
                "GENERATED — run senpi-trading-runtime/scripts/behaviour_hash.py --write. Maps each "
                "strategy package to its strategy.yaml version and a hash of the behaviour the "
                "runtime executes (parsed YAML + Python AST without docstrings; comments and prose "
                "are excluded). strategies/tests/test_version_tracks_behaviour.py recomputes these "
                "and fails when a package's behaviour changed while its version did not — because "
                "deploy.py refreshes a package on VERSION INEQUALITY only, so a behavioural fix "
                "shipped without a bump never reaches a box already holding that version."),
            "packages": {k: {"version": v["version"], "behaviour": v["behaviour"]}
                         for k, v in pkgs.items()},
        }
        with open(LOCK_PATH, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"wrote {LOCK_PATH} — {len(pkgs)} packages")
        return 0

    for sid, v in pkgs.items():
        print(f"{sid:24} {v['version']:10} {v['behaviour']}  ({v['files']} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
