#!/usr/bin/env python3
"""Sync the README version column from each skill's SKILL.md metadata.version.

metadata.version in every senpi-*/SKILL.md is the single source of truth. This
script rewrites the version cell of each skill row in README.md to match it, so
the table can never drift by hand again.

It fails loudly (never silently) in all drift directions:
  * README row version is stale             -> rewritten (--check exits 1)
  * README row's SKILL.md has no version    -> exit 2
  * skill has a SKILL.md but no README row  -> exit 2

Dirs without a SKILL.md are not distributed skills and are ignored.

Usage:
  python3 scripts/gen_readme_versions.py            # rewrite README.md in place
  python3 scripts/gen_readme_versions.py --check    # exit 1 if out of sync (CI)

Stdlib only, no dependencies.
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

# A skill row: | [`senpi-x`](senpi-x/) | 1.2.3 | description... |
# The version cell is "anything but a pipe" rather than a semver triple, so a
# malformed / empty / pre-release cell gets corrected instead of silently
# falling out of the regex and being skipped.
ROW = re.compile(r'^(\| \[`(senpi-[a-z0-9-]+)`\]\([^)]*\) \| )([^|]*?)( \| )')

# YAML frontmatter, line-anchored: a `---` inside a folded description block
# cannot truncate the parse.
FRONT = re.compile(r'\A---[ \t]*\r?\n(.*?)^---[ \t]*$', re.S | re.M)

# Captures the whole value including any pre-release suffix, so `2.12.0-rc1` can
# never be silently truncated to `2.12.0`.
VER = re.compile(r'^[ \t]*version:[ \t]*["\']?([^"\'\s]+)["\']?[ \t]*$', re.M)


def skill_version(skill_id: str):
    sk = ROOT / skill_id / "SKILL.md"
    if not sk.exists():
        return None
    front = FRONT.match(sk.read_text(encoding="utf-8"))
    if not front:
        return None
    m = VER.search(front.group(1))
    return m.group(1) if m else None


def skills_on_disk() -> set:
    """Every distributed skill = a senpi-*/ dir that has a SKILL.md."""
    return {p.parent.name for p in ROOT.glob("senpi-*/SKILL.md")}


def main() -> int:
    check = "--check" in sys.argv
    lines = README.read_text(encoding="utf-8").splitlines(keepends=True)
    out, drifted, missing, seen = [], [], [], set()
    for line in lines:
        m = ROW.match(line)
        if not m:
            out.append(line); continue
        skill_id, cur = m.group(2), m.group(3)
        seen.add(skill_id)
        actual = skill_version(skill_id)
        if actual is None:
            missing.append(skill_id); out.append(line); continue
        if actual != cur:
            drifted.append(f"{skill_id}: {cur or '(empty)'} -> {actual}")
            line = ROW.sub(rf'\g<1>{actual}\g<4>', line, count=1)
        out.append(line)

    if missing:
        print("ERROR: no metadata.version found for: " + ", ".join(sorted(missing)),
              file=sys.stderr)
        return 2
    unlisted = skills_on_disk() - seen
    if unlisted:
        print("ERROR: skill(s) have a SKILL.md but no README table row: "
              + ", ".join(sorted(unlisted)), file=sys.stderr)
        print("Add a row to the skill table in README.md.", file=sys.stderr)
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
