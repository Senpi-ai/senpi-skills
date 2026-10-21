#!/usr/bin/env python3
"""Content-conservation check for a SKILL.md restructure.

Every sentence of the old SKILL.md must still exist in the new SKILL.md or in the skill's
references/*.md, or be listed in a dropped-list file with a reason. Reviewers read the dropped
list instead of a 90k diff (skills context-budget spec, section 5.1).

Imperatives stay in SKILL.md core (the maintainer's rule), so moving one to a reference is not
enough on its own: an old sentence that carries an imperative word (never, always, must, do not,
don't, only, verbatim) and is now found only in references/ must also be quoted under a
"## Imperatives that left core" heading in the dropped list, so the reviewer sees and approves
each one. The check prints every such sentence with the reference that holds it.

Usage:
  check_conservation.py --old <path | git-ref:path> --skill-dir <dir> [--dropped <md>]

  --old       the pre-restructure SKILL.md: a file path, or <ref>:<path> read with `git show`
  --skill-dir the restructured skill directory (SKILL.md + references/*.md)
  --dropped   markdown listing each deliberately removed or reworded sentence with its reason

Matching is on normalized text: markdown markers (* _ ` | > #), list bullets and whitespace runs
are removed, case is folded, and a sentence counts as kept when it is a substring of the
normalized new corpus. Fragments under 4 words (headings, labels) are ignored.
Exit 0 when everything is accounted for, 1 otherwise (a missing sentence, or an imperative that
left core without being listed).
"""
import argparse
import os
import re
import subprocess
import sys

MIN_WORDS = 4
IMPERATIVE = re.compile(r"\b(never|always|must|do not|don't|only|verbatim)\b")
LEFT_CORE_HEADING = "## Imperatives that left core"


def normalize(text):
    text = re.sub(r"[*_`|>#]", " ", text)
    text = re.sub(r"^\s*(?:[-+]|\d+[.)])\s+", " ", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip().lower()


def sentences(text):
    out = []
    for line in text.splitlines():
        for cell in line.split("|"):
            for part in re.split(r"(?<=[.!?])\s+", cell):
                norm = normalize(part)
                if len(norm.split()) >= MIN_WORDS:
                    out.append(norm)
    return out


def read_old(spec):
    if os.path.exists(spec):
        with open(spec, encoding="utf-8") as f:
            return f.read()
    return subprocess.run(["git", "show", spec], check=True, capture_output=True, text=True).stdout


def read_skill(skill_dir):
    """(normalized SKILL.md, {reference name: normalized text})."""
    with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
        core = normalize(f.read())
    refs = {}
    ref_dir = os.path.join(skill_dir, "references")
    if os.path.isdir(ref_dir):
        for name in sorted(os.listdir(ref_dir)):
            if name.endswith(".md"):
                with open(os.path.join(ref_dir, name), encoding="utf-8") as f:
                    refs[name] = normalize(f.read())
    return core, refs


def left_core_section(dropped_text):
    """The normalized body of the dropped list's "## Imperatives that left core" section."""
    lines = dropped_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == LEFT_CORE_HEADING:
            body = []
            for rest in lines[i + 1:]:
                if re.match(r"^#{1,2} ", rest):
                    break
                body.append(rest)
            return normalize("\n".join(body))
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True)
    ap.add_argument("--skill-dir", required=True)
    ap.add_argument("--dropped")
    args = ap.parse_args()

    core, refs = read_skill(args.skill_dir)
    corpus = normalize(" ".join([core, *refs.values()]))
    dropped, left_core = "", ""
    if args.dropped:
        with open(args.dropped, encoding="utf-8") as f:
            raw = f.read()
        dropped, left_core = normalize(raw), left_core_section(raw)

    seen, missing, moved, unlisted = set(), [], [], []
    old = sentences(read_old(args.old))
    for s in old:
        if s in seen:
            continue
        seen.add(s)
        if s not in corpus and s not in dropped:
            missing.append(s)
        elif s not in core and IMPERATIVE.search(s):
            where = [name for name, text in refs.items() if s in text]
            if where:
                moved.append((s, where))
                if s not in left_core:
                    unlisted.append((s, where))

    print(f"{len(seen)} distinct sentences checked; {len(missing)} unaccounted for; "
          f"{len(moved)} imperatives left core for references/, {len(unlisted)} of them not listed "
          f"under \"{LEFT_CORE_HEADING}\"")
    for s in missing:
        print(f"  MISSING: {s}")
    for s, where in unlisted:
        print(f"  LEFT CORE, NOT LISTED [{', '.join(where)}]: {s}")
    return 1 if missing or unlisted else 0


if __name__ == "__main__":
    sys.exit(main())
