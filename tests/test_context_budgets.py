"""Context-budget gate: skill text must fit what OpenClaw will actually serve.

Design spec:  docs/superpowers/specs/2026-10-09-skills-context-budget-restructure-design.md (§3, §8)
Migration:    docs/superpowers/plans/2026-10-16-openclaw-10.3-migration/handoffs/skills-restructure-10x.md (§1)
(both in the senpi-personal-agents workspace, not this repo).

Checks, all measured the way OpenClaw measures (tests/context_budget/openclaw_measure.py):
  1. Every */SKILL.md <= 56,000 est chars (hard). OpenClaw 10.4 refuses a SKILL.md read over the
     model's cap whole (64,000 for the fleet's GLM/Gemini models) and SKILL.md reads cannot be paged.
     The only exceptions are the `absolute_56k` entries in tests/context_budget_allowlist.json,
     which can only shrink and go away.
  2. SKILL.md and references/*.md over 16,000 get a warning, never a failure: 16,000 is the 5.7
     follow-up-turn replay cap, an optional goal worth reaching only where it is low-hanging.
     What a skill over it must keep inside 5.7's replay is asserted per skill on
     openclaw_measure.replay_57 (the 5.7 code, ported), not here.
  3. The <available_skills> catalog (5.7 full format) plus the OpenClaw-bundled entries prod also
     renders stays <= 30,000, under the 32,000 maxSkillsPromptChars the senpi-agent bootstrap writes.
     Over 32,000 every box drops all descriptions and routes skills by name alone.

Print the current numbers: python3 tests/test_context_budgets.py
"""
import glob
import json
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_budget"))
import openclaw_measure as om  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST_PATH = os.path.join(REPO, "tests", "context_budget_allowlist.json")
ALLOWLIST_REL = "tests/context_budget_allowlist.json"

# ---- Constants: pinned to OpenClaw 2026.5.7 (prod) and 2026.10.4 (migration target). ----------
# Re-derive all of them on any OpenClaw bump or any change to the senpi-agent skills config.

# 10.4 SKILL.md read cap for the fleet's models is 64,000; 8,000 margin for growth between a merge
# and the next release and for estimator drift.
SKILL_MD_CEILING = 56_000
# 5.7 replays at most 16,000 chars of a tool result on follow-up turns (openclaw_measure.replay_57:
# the first ~15,9xx, or head ~11,8xx + tail ~3,9xx when the last 2,000 chars look like a result).
OPTIONAL_GOAL = 16_000

# senpi-agent src/bootstrap.mjs writes skills.limits.maxSkillsPromptChars = 32,000.
PROD_MAX_SKILLS_PROMPT_CHARS = 32_000
CATALOG_CEILING = 30_000
# Catalog chars that prod renders for skills that are not ours (OpenClaw-bundled, plugin skills).
# Derivation: Jason measured the prod block at 28,815 chars, 22 entries, on 2026-10-12. Our 15
# skills at senpi-skills main as of 2026-10-12 12:00 UTC (4f0a5370, via
# `git rev-list -1 --first-parent --before=2026-10-12T12:00:00Z origin/main`) render 26,267 here,
# so the rest is 28,815 - 26,267 = 2,548 (7 entries).
# Sanity check: in the openclaw@2026.5.7 tarball the bundled skills a box can load (no required
# bin/env/config, or only `curl`: healthcheck, node-connect, skill-creator, taskflow,
# taskflow-inbox-triage, weather) render 1,488 chars, and 1,488 + ours at ef6bfbb (24,968) =
# 26,456, exactly the prod figure recorded in senpi-agent bootstrap.mjs for 2026-09-23
# (15 managed + 6 bundled). The 7th entry on 2026-10-12 (~1,060 chars) was not identified;
# keeping it in the reserve errs on the safe side.
BUNDLED_RESERVE_CHARS = 28_815 - 26_267

# The only files ever allowed over SKILL_MD_CEILING, at their size on b1916b12. The allowlist file
# may hold a subset of these at or below these sizes, nothing else.
GRANDFATHERED_OVER_CEILING = {
    "senpi-improve-trades/SKILL.md": 63_956,
}

RESTRUCTURE_FIX = (
    "Fix: move rationale, worked examples and long-tail cases into references/<topic>.md (those are "
    "paged on 10.x, not refused) and leave every imperative in SKILL.md, per the placement rules in "
    "the restructure spec. Do not shorten the description: it routes the skill."
)


def _rel(path):
    return os.path.relpath(path, REPO).replace(os.sep, "/")


# 15 skills on b1916b12. An empty discovery would skip every parametrized test and leave the
# catalog check measuring only the reserve, a silent green.
MIN_SKILL_DIRS = 10


def _skill_dirs():
    return om.repo_skill_dirs(REPO)


def test_skill_discovery_finds_the_skills():
    assert len(_skill_dirs()) >= MIN_SKILL_DIRS, (
        f"repo_skill_dirs found {len(_skill_dirs())} skills under {REPO}; expected at least {MIN_SKILL_DIRS}")


def _skill_mds():
    return [os.path.join(d, "SKILL.md") for d in _skill_dirs()]


def _references():
    out = []
    for d in _skill_dirs():
        out.extend(sorted(glob.glob(os.path.join(d, "references", "*.md"))))
    return out


def _allowlist():
    if not os.path.exists(ALLOWLIST_PATH):
        return {}
    with open(ALLOWLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _absolute_allowlist():
    # Absent or empty section = strict: no file may exceed the ceiling.
    return _allowlist().get("absolute_56k") or {}


# ---- 1. Absolute SKILL.md ceiling ------------------------------------------------------------

@pytest.mark.parametrize("path", _skill_mds(), ids=_rel)
def test_skill_md_under_absolute_ceiling(path):
    rel = _rel(path)
    size = om.estimate_file_chars(path)
    allowed = _absolute_allowlist().get(rel)
    if allowed is None:
        assert size <= SKILL_MD_CEILING, (
            f"{rel} is {size:,} est chars; the ceiling is {SKILL_MD_CEILING:,}. OpenClaw 10.4 refuses "
            f"a SKILL.md over the model's 64,000 cap whole and the agent cannot page it, so the skill "
            f"would not load at all. {RESTRUCTURE_FIX} There is no allowlist for new files: "
            f"{ALLOWLIST_REL} absolute_56k only shrinks."
        )
    else:
        assert size <= allowed, (
            f"{rel} is {size:,} est chars, above its recorded {allowed:,} in {ALLOWLIST_REL} "
            f"(absolute_56k). This file is only allowed over the {SKILL_MD_CEILING:,} ceiling until its "
            f"restructure lands, and it may not grow meanwhile. Cut it back to {allowed:,} or less, or "
            f"restructure it under {SKILL_MD_CEILING:,}. Do not raise the entry. {RESTRUCTURE_FIX}"
        )


def test_absolute_allowlist_only_shrinks():
    section = _absolute_allowlist()
    for rel, recorded in sorted(section.items()):
        path = os.path.join(REPO, rel)
        cap = GRANDFATHERED_OVER_CEILING.get(rel)
        assert cap is not None, (
            f"{ALLOWLIST_REL} absolute_56k lists {rel}, which was never over the {SKILL_MD_CEILING:,} "
            f"ceiling. The section only shrinks: remove the entry and bring {rel} under "
            f"{SKILL_MD_CEILING:,}. {RESTRUCTURE_FIX}"
        )
        assert isinstance(recorded, int) and recorded <= cap, (
            f"{ALLOWLIST_REL} absolute_56k records {rel} at {recorded!r}; it may not exceed {cap:,} "
            f"(its size when the gate landed). Lower the entry back to {cap:,} or less."
        )
        assert os.path.isfile(path), (
            f"{ALLOWLIST_REL} absolute_56k lists {rel}, which no longer exists. Remove the entry."
        )
        size = om.estimate_file_chars(path)
        assert size > SKILL_MD_CEILING, (
            f"{rel} is now {size:,} est chars, under the {SKILL_MD_CEILING:,} ceiling. Remove its entry "
            f"from {ALLOWLIST_REL} absolute_56k in this PR so the ceiling holds it from now on "
            f"(delete the whole section once it is empty)."
        )


# ---- 2. 16,000: optional goal, warning only ----------------------------------------------------

def _warn_over_goal(path, kind, trim):
    size = om.estimate_file_chars(path)
    if size > OPTIONAL_GOAL:
        warnings.warn(
            f"{_rel(path)} is {size:,} est chars, over the {OPTIONAL_GOAL:,} {kind} goal (on follow-up "
            f"turns 5.7 replays only 16,000 chars of a file read). Optional goal, low-hanging only: "
            f"{trim}; otherwise leave it. Not a failure.",
            UserWarning,
        )
    return size


@pytest.mark.parametrize("path", _skill_mds(), ids=_rel)
def test_skill_md_optional_goal(path):
    _warn_over_goal(path, "SKILL.md", "move a section that lifts out cleanly into references/")


@pytest.mark.parametrize("path", _references(), ids=_rel)
def test_reference_optional_goal(path):
    _warn_over_goal(path, "references/*.md", "split a self-contained section into its own reference")


# ---- 3. Skills catalog --------------------------------------------------------------------------

def catalog_numbers():
    ours = om.catalog_chars(_skill_dirs())
    total = ours + BUNDLED_RESERVE_CHARS
    return ours, total, CATALOG_CEILING - total


def test_catalog_fits_full_format():
    ours, total, headroom = catalog_numbers()
    assert total <= CATALOG_CEILING, (
        f"The skills catalog would render {total:,} chars on a prod box: our {len(_skill_dirs())} skills "
        f"{ours:,} + {BUNDLED_RESERVE_CHARS:,} reserved for OpenClaw-bundled entries; the ceiling is "
        f"{CATALOG_CEILING:,} (prod maxSkillsPromptChars is {PROD_MAX_SKILLS_PROMPT_CHARS:,}; above "
        f"it every box drops ALL descriptions and routes skills by name only). Measured as OpenClaw "
        f"2026.5.7 formatSkillsForPrompt renders it: frontmatter name + description + location, "
        f"XML-escaped. Fix: shorten the description you added or grew (move trigger detail into the "
        f"SKILL.md body). No fleet limit bump (spec §9, decision 3). Headroom is {headroom:,}."
    )


def main():
    print(f"{'est chars':>10}  file")
    for path in _skill_mds() + _references():
        size = om.estimate_file_chars(path)
        flag = ""
        if size > SKILL_MD_CEILING and path.endswith("SKILL.md"):
            flag = "  OVER CEILING" + (" (allowlisted)" if _rel(path) in _absolute_allowlist() else "")
        elif size > OPTIONAL_GOAL:
            flag = "  > 16k (optional goal)"
        print(f"{size:>10,}  {_rel(path)}{flag}")
    ours, total, headroom = catalog_numbers()
    print(f"\ncatalog: ours {ours:,} + bundled reserve {BUNDLED_RESERVE_CHARS:,} = {total:,} "
          f"(ceiling {CATALOG_CEILING:,}, headroom {headroom:,})")


if __name__ == "__main__":
    main()
