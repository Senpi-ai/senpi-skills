#!/usr/bin/env python3
"""Sync the README version column from each skill's SKILL.md metadata.version.

metadata.version in every senpi-*/SKILL.md is the single source of truth. This
script rewrites the version cell of each skill row in README.md to match it, so
the table can never drift by hand again.

Usage:
  python3 scripts/gen_readme_versions.py            # rewrite README.md in place
  python3 scripts/gen_readme_versions.py --check    # exit 1 if out of sync (CI)

Stdlib only, no dependencies.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# A skill row: | [`senpi-x`](senpi-x/) | 1.2.3 | description... |
ROW = re.compile(r'^(\| \[`(senpi-[a-z0-9-]+)`\]\([^)]*\) \| )([0-9]+\.[0-9]+\.[0-9]+)( \| )')
VER = re.compile(r'^\s*version:\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)["\']?', re.M)


def skill_version(skill_id: str):
    sk = ROOT / skill_id / "SKILL.md"
    if not sk.exists():
        return None
    # only the YAML frontmatter (between the first two --- fences)
    text = sk.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    front = parts[1] if len(parts) >= 3 else text[:2000]
    m = VER.search(front)
    return m.group(1) if m else None


def main() -> int:
    check = "--check" in sys.argv
    lines = README.read_text(encoding="utf-8").splitlines(keepends=True)
    out, drifted, missing = [], [], []
    for line in lines:
        m = ROW.match(line)
        if not m:
            out.append(line); continue
        skill_id, cur = m.group(2), m.group(3)
        actual = skill_version(skill_id)
        if actual is None:
            missing.append(skill_id); out.append(line); continue
        if actual != cur:
            drifted.append(f"{skill_id}: {cur} -> {actual}")
            line = ROW.sub(rf'\g<1>{actual}\g<4>', line, count=1)
        out.append(line)

    if missing:
        print("ERROR: no metadata.version found for: " + ", ".join(missing), file=sys.stderr)
        return 2
    if check:
        if drifted:
            print("README version table is OUT OF SYNC with SKILL.md:", file=sys.stderr)
            for d in drifted: print("  " + d, file=sys.stderr)
            print("\nRun:  python3 scripts/gen_readme_versions.py", file=sys.stderr)
            return 1
        print("README versions in sync ✓")
        return 0
    README.write_text("".join(out), encoding="utf-8")
    if drifted:
        print("Updated README versions:")
        for d in drifted: print("  " + d)
    else:
        print("README already in sync ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
