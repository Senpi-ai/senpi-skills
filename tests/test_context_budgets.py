"""Context-budget gate: skill text must fit what OpenClaw will actually serve.

Design spec:  docs/superpowers/specs/2026-10-09-skills-context-budget-restructure-design.md (§3, §8)
Migration:    docs/superpowers/plans/2026-10-16-openclaw-10.3-migration/handoffs/skills-restructure-10x.md (§1)
(both in the senpi-personal-agents workspace, not this repo).

Checks, all measured the way OpenClaw measures (tests/context_budget/openclaw_measure.py):
  1. Every */SKILL.md <= 56,000 est chars (hard, no exceptions). OpenClaw 10.4 refuses a SKILL.md
     read over the model's cap whole (64,000 for the fleet's GLM/Gemini models) and SKILL.md reads
     cannot be paged. There is no allowlist: a file over the ceiling is restructured, not excused.
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
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_budget"))
import openclaw_measure as om  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The allowlist that held the over-ceiling files until their restructures landed. It must not come back.
RETIRED_ALLOWLIST_REL = "tests/context_budget_allowlist.json"

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

RESTRUCTURE_FIX = (
    "Fix: move rationale, worked examples and long-tail cases into references/<topic>.md (those are "
    "paged on 10.x, not refused) and leave every imperative in SKILL.md, per the placement rules in "
    "the restructure spec. Do not shorten the description: it routes the skill."
)


def _rel(path):
    return os.path.relpath(path, REPO).replace(os.sep, "/")


def _skill_dirs():
    return om.repo_skill_dirs(REPO)


def _skill_mds():
    return [os.path.join(d, "SKILL.md") for d in _skill_dirs()]


def _references():
    out = []
    for d in _skill_dirs():
        out.extend(sorted(glob.glob(os.path.join(d, "references", "*.md"))))
    return out


# ---- 1. Absolute SKILL.md ceiling ------------------------------------------------------------

@pytest.mark.parametrize("path", _skill_mds(), ids=_rel)
def test_skill_md_under_absolute_ceiling(path):
    rel = _rel(path)
    size = om.estimate_file_chars(path)
    assert size <= SKILL_MD_CEILING, (
        f"{rel} is {size:,} est chars; the ceiling is {SKILL_MD_CEILING:,}. OpenClaw 10.4 refuses "
        f"a SKILL.md over the model's 64,000 cap whole and the agent cannot page it, so the skill "
        f"would not load at all. {RESTRUCTURE_FIX} The ceiling has no exceptions."
    )


def test_the_ceiling_has_no_allowlist():
    assert not os.path.exists(os.path.join(REPO, RETIRED_ALLOWLIST_REL)), (
        f"{RETIRED_ALLOWLIST_REL} exists again. The 56,000 ceiling has no exceptions (migration gate "
        f"U4): delete the file and restructure the SKILL.md that is over it. {RESTRUCTURE_FIX}"
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
            flag = "  OVER CEILING"
        elif size > OPTIONAL_GOAL:
            flag = "  > 16k (optional goal)"
        print(f"{size:>10,}  {_rel(path)}{flag}")
    ours, total, headroom = catalog_numbers()
    print(f"\ncatalog: ours {ours:,} + bundled reserve {BUNDLED_RESERVE_CHARS:,} = {total:,} "
          f"(ceiling {CATALOG_CEILING:,}, headroom {headroom:,})")


if __name__ == "__main__":
    main()
