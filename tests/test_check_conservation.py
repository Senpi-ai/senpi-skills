"""Smoke test for .github/scripts/check_conservation.py, the content-conservation tool every
SKILL.md restructure runs by hand (it is not a CI step): exit 0 when every old sentence survives,
exit 1 when one is missing and not listed as dropped, or when an imperative moved to a reference
without being listed under "## Imperatives that left core"."""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, ".github", "scripts", "check_conservation.py")

OLD = """---
name: demo
description: A demo skill.
---
# Demo

Always run the engine first and relay its text verbatim.
Never guess a wallet address when the user has several.
| Rule | Never split one strategy into two rows. |
The engine sorts the wallet list by value before it prints.
"""


def _run(old_path, skill_dir, dropped=None):
    cmd = [sys.executable, SCRIPT, "--old", str(old_path), "--skill-dir", str(skill_dir)]
    if dropped:
        cmd += ["--dropped", str(dropped)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _skill(tmp_path, text, refs=None):
    d = tmp_path / "skill"
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    for name, body in (refs or {}).items():
        (d / "references" / name).write_text(body, encoding="utf-8")
    old = tmp_path / "old.md"
    old.write_text(OLD, encoding="utf-8")
    return old, d


def test_identical_copy_passes(tmp_path):
    old, d = _skill(tmp_path, OLD)
    r = _run(old, d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 unaccounted for" in r.stdout


def test_text_moved_to_a_reference_passes(tmp_path):
    line = "The engine sorts the wallet list by value before it prints.\n"
    old, d = _skill(tmp_path, OLD.replace(line, ""), {"wallets.md": line})
    assert _run(old, d).returncode == 0


GUESS = "Never guess a wallet address when the user has several.\n"


def test_imperative_moved_to_a_reference_fails_until_listed(tmp_path):
    old, d = _skill(tmp_path, OLD.replace(GUESS, ""), {"wallets.md": GUESS})
    r = _run(old, d)
    assert r.returncode == 1
    assert "LEFT CORE, NOT LISTED [wallets.md]: never guess a wallet address" in r.stdout

    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text('## Reworded\n- "Never guess a wallet address when the user has several."\n', encoding="utf-8")
    assert _run(old, d, elsewhere).returncode == 1

    listed = tmp_path / "listed.md"
    listed.write_text('## Imperatives that left core\n- "Never guess a wallet address when the user has '
                      'several." -> references/wallets.md\n', encoding="utf-8")
    r = _run(old, d, listed)
    assert r.returncode == 0, r.stdout
    assert "1 imperatives left core for references/, 0 of them not listed" in r.stdout


def test_truncated_copy_fails(tmp_path):
    old, d = _skill(tmp_path, OLD[: OLD.index("Never guess")])
    r = _run(old, d)
    assert r.returncode == 1
    assert "MISSING: never guess a wallet address" in r.stdout


def test_dropped_list_accounts_for_a_removed_sentence(tmp_path):
    old, d = _skill(tmp_path, OLD.replace(GUESS, ""))
    dropped = tmp_path / "dropped.md"
    dropped.write_text('- "Never guess a wallet address when the user has several." Reason: duplicate.\n', encoding="utf-8")
    assert _run(old, d, dropped).returncode == 0


def test_typographic_apostrophe_is_an_imperative(tmp_path):
    # "Don’t" (U+2019) must count as "don't": moved to a reference, it needs listing.
    old, d = _skill(tmp_path, "# Demo\n", {"r.md": OLD.replace("Never guess", "Don’t guess")})
    old.write_text(OLD.replace("Never guess", "Don’t guess"), encoding="utf-8")
    r = _run(old, d)
    assert r.returncode == 1 and "don't guess a wallet address" in r.stdout


def test_no_match_across_two_files(tmp_path):
    # The sentence's first half ends SKILL.md, its second half starts a reference: not kept.
    core = OLD.replace("The engine sorts the wallet list by value before it prints.\n", "The engine sorts the\n")
    old, d = _skill(tmp_path, core, {"r.md": "wallet list by value before it prints.\n"})
    r = _run(old, d)
    assert r.returncode == 1 and "MISSING: the engine sorts the wallet list" in r.stdout
