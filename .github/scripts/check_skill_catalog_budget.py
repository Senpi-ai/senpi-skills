#!/usr/bin/env python3
"""Estimate the size of the skills catalog OpenClaw puts in the system prompt, and fail CI if it
would stop fitting.

OpenClaw renders every eligible skill into one `<available_skills>` XML block:

    <skill>
      <name>quant-desk</name>
      <description>…the SKILL.md frontmatter `description`, XML-escaped…</description>
      <location>/data/.openclaw/skills/quant-desk/SKILL.md</location>
    </skill>

and checks the whole block against `skills.limits.maxSkillsPromptChars` (default 18,000). The
fallback is ALL-OR-NOTHING: when the full block does not fit, EVERY skill is rendered in compact form
(name + location, no description). The description is the only thing the model routes on, so one
over-long description silently degrades routing for all of them, not just its own skill. Prod ran
compact from senpi-skills#654 (2026-09-17) until senpi-agent#82 raised the limit to 32,000 as a
stopgap.

Counted the way OpenClaw counts: the description AFTER XML escaping (a `"` costs 6 chars as
`&quot;`, which is why quoted trigger lists are expensive), plus the per-skill tag overhead, plus the
bundled OpenClaw skills that ship in the image and share the same budget (estimated — they are not
in this repo).

    python3 .github/scripts/check_skill_catalog_budget.py          # report + gate
    python3 .github/scripts/check_skill_catalog_budget.py --quiet  # gate only
"""
import argparse
import glob
import os
import sys

import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# OpenClaw's DEFAULT maxSkillsPromptChars. Above this every skill loses its description (compact
# fallback). This is the number to be under; see the TODO on CATALOG_BUDGET.
OPENCLAW_DEFAULT_LIMIT = 18_000
# The limit senpi-agent#82 sets on senpi boxes. Exceeding it puts prod back into compact fallback.
OPENCLAW_SENPI_LIMIT = 32_000

# The gate. The full estimated catalog (managed + bundled + XML) must stay at or below this.
# TODO: get back under OPENCLAW_DEFAULT_LIMIT (18,000), then lower this to ~17,000 so a box that
# drops the senpi-agent#82 override still routes on descriptions. It sits at the raised limit minus
# a safety margin for now because the catalog is still well over 18,000 after the first trim.
CATALOG_BUDGET = 28_000

# Per-skill soft cap on the escaped description. A WARNING, not a failure: several skills were
# already over it when the gate landed, and trimming them is routing work that needs its own review.
# The catalog total is the hard gate; this points at where to cut next.
PER_SKILL_SOFT_CAP = 1_200

# Where OpenClaw installs managed skills on a senpi box (OPENCLAW_STATE_DIR=/data/.openclaw).
LOCATION = "/data/.openclaw/skills/{name}/SKILL.md"

# Bundled OpenClaw skills that are eligible on a senpi box and share the budget. Not in this repo,
# so estimated: 6 skills, ~1,500 chars of descriptions between them, bundled-path locations.
BUNDLED_SKILLS = 6
BUNDLED_DESCRIPTION_CHARS = 1_500
BUNDLED_LOCATION_CHARS = len("/openclaw/skills/xxxxxxxxxxxx/SKILL.md")

# The fixed preamble + <available_skills> wrapper OpenClaw emits around the entries.
HEADER_CHARS = 368


def escape_xml(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def entry_chars(name, description_chars, location):
    """One rendered <skill> entry, newline-joined as OpenClaw emits it."""
    return len("\n".join([
        "  <skill>",
        f"    <name>{name}</name>",
        "    <description>" + "x" * description_chars + "</description>",
        f"    <location>{location}</location>",
        "  </skill>",
    ])) + 1


def load_skills(repo=REPO):
    out = []
    for path in sorted(glob.glob(os.path.join(repo, "*", "SKILL.md"))):
        text = open(path, encoding="utf-8").read()
        if not text.startswith("---\n"):
            raise SystemExit(f"{path}: no YAML frontmatter")
        front = yaml.safe_load(text.split("\n---", 2)[0][4:]) or {}
        name = front.get("name") or os.path.basename(os.path.dirname(path))
        desc = front.get("description") or ""
        out.append({"name": name, "raw": len(desc), "escaped": len(escape_xml(desc))})
    return out


def estimate(skills):
    managed = sum(entry_chars(s["name"], s["escaped"], LOCATION.format(name=s["name"])) for s in skills)
    bundled = BUNDLED_SKILLS * entry_chars("x" * 12, BUNDLED_DESCRIPTION_CHARS // BUNDLED_SKILLS,
                                           BUNDLED_LOCATION_CHARS * "x")
    return managed, bundled, HEADER_CHARS + managed + bundled


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    skills = load_skills()
    managed, bundled, total = estimate(skills)

    if not args.quiet:
        print(f"{'skill':34} {'desc':>6} {'escaped':>8}")
        for s in sorted(skills, key=lambda s: -s["escaped"]):
            flag = "  > soft cap" if s["escaped"] > PER_SKILL_SOFT_CAP else ""
            print(f"{s['name']:34} {s['raw']:>6} {s['escaped']:>8}{flag}")
        print(f"{'descriptions (raw / escaped)':34} {sum(s['raw'] for s in skills):>6} "
              f"{sum(s['escaped'] for s in skills):>8}")
        print(f"managed entries {managed} + bundled estimate {bundled} + header {HEADER_CHARS} "
              f"= {total} (budget {CATALOG_BUDGET}; OpenClaw default {OPENCLAW_DEFAULT_LIMIT}, "
              f"senpi {OPENCLAW_SENPI_LIMIT})")

    for s in skills:
        if s["escaped"] > PER_SKILL_SOFT_CAP:
            print(f"::warning file={s['name']}/SKILL.md::{s['name']} description is {s['escaped']} "
                  f"chars escaped (soft cap {PER_SKILL_SOFT_CAP}) — keep it to routing: what it does, "
                  f"trigger phrases, which sibling to use instead; move instructions to the body")

    if total > CATALOG_BUDGET:
        print(f"::error::estimated skills catalog is {total} chars, over the {CATALOG_BUDGET} budget. "
              f"Over {OPENCLAW_SENPI_LIMIT} (or {OPENCLAW_DEFAULT_LIMIT} on a default box) OpenClaw drops "
              f"EVERY skill description from the prompt. Trim `description` frontmatter — the "
              f"largest are listed above.")
        return 1
    if total > OPENCLAW_DEFAULT_LIMIT and not args.quiet:
        print(f"note: over the OpenClaw default ({OPENCLAW_DEFAULT_LIMIT}) — descriptions only render "
              f"on boxes carrying the senpi-agent#82 override")
    return 0


if __name__ == "__main__":
    sys.exit(main())
