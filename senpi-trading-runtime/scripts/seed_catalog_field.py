#!/usr/bin/env python3
"""Write one page-copy field (descriptor, summary or risks) into many strategy.yaml `catalog:` blocks.

    python3 senpi-trading-runtime/scripts/seed_catalog_field.py descriptor drafts.json [--overwrite]

drafts.json maps strategy id -> text. Each value is inserted as one double-quoted line right after
the block's `name:` line. The file is edited as TEXT, never re-dumped, so comments and layout
survive (as senpi-strategy-ops/scripts/_fork.py edits). A package that already has the field is
left alone unless --overwrite; a value that breaks the field's rule (gen_catalog.page_copy_problems)
is skipped and reported. Run gen_catalog.py afterwards and commit both catalog copies.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import argparse
import json
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_catalog  # noqa: E402

FIELDS = ("descriptor", "summary", "risks")


def seed(text, field, value, overwrite):
    """(new_text, outcome) for one strategy.yaml's text."""
    lines = text.splitlines(keepends=True)
    try:
        cat_at = next(i for i, ln in enumerate(lines) if re.match(r"^catalog:\s*(#.*)?$", ln))
    except StopIteration:
        return text, "no catalog: block"
    name_at, indent, existing = None, None, None
    for i in range(cat_at + 1, len(lines)):
        ln = lines[i]
        if ln.strip() and not ln.startswith((" ", "\t")):
            break  # left the block
        m = re.match(r"^(\s+)(\w+):", ln)
        if not m:
            continue
        indent = indent or m.group(1)
        if m.group(1) != indent:
            continue  # a nested key
        if m.group(2) == "name" and name_at is None:
            name_at = i
        if m.group(2) == field:
            existing = i
    if name_at is None:
        return text, "no catalog.name line"
    line = f"{indent}{field}: {json.dumps(value, ensure_ascii=False)}\n"
    if existing is not None:
        if not overwrite:
            return text, "already set"
        if lines[existing].rstrip().endswith((">", "|", ">-", "|-")):
            return text, "already set as a block scalar; edit by hand"
        lines[existing] = line
        return "".join(lines), "overwritten"
    lines.insert(name_at + 1, line)
    return "".join(lines), "written"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("field", choices=FIELDS)
    ap.add_argument("drafts", help="JSON object: strategy id -> text")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    with open(a.drafts) as f:
        drafts = json.load(f)
    counts = {}
    for sid, value in sorted(drafts.items()):
        path = os.path.join(gen_catalog.REPO_ROOT, "strategies", sid, "strategy.yaml")
        if not os.path.exists(path):
            outcome = "no such package"
        elif not isinstance(value, str) or (
                problems := gen_catalog.page_copy_problems({a.field: value})):
            outcome = f"skipped: {'; '.join(problems) if isinstance(value, str) else 'not a string'}"
        else:
            with open(path) as f:
                text = f.read()
            new, outcome = seed(text, a.field, value, a.overwrite)
            if new != text:
                parsed = (yaml.safe_load(new) or {}).get("catalog") or {}
                if parsed.get(a.field) != value:
                    outcome = "refused: the edit did not parse back to the value"
                else:
                    with open(path, "w") as f:
                        f.write(new)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome not in ("written", "overwritten"):
            print(f"  {sid}: {outcome}", file=sys.stderr)
    print(", ".join(f"{n} {k}" for k, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
