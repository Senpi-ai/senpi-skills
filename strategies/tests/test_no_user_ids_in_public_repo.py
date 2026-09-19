"""No real Senpi user id may be committed to this repo. It is public.

This has been cleaned before and came back — 85 occurrences by 2026-09-19, and 70 of those were
one copy-pasted comment that paired a real **name** with a real **id**, duplicated across 70
template `runtime.yaml` files as the templates were forked from one another. Pairing the two is the
part that matters: an id alone is opaque to an outsider, an id next to a name is not.

The comments themselves are worth keeping — they are what makes a rule evidence rather than
opinion ("found live", not "we think"). So the fix is never to delete the finding, only the
identifier: keep the date, the asset, the strategy and the measurement, drop the who.

Synthetic ids used as fixtures are fine and are listed below; they are obviously not real.

Run: python3 -m pytest strategies/tests/test_no_user_ids_in_public_repo.py -q
"""
import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MID = re.compile(r"\bM\d{6}\b")
# Placeholders. Deliberately unmistakable, and used as test data rather than as evidence.
SYNTHETIC = {"M123456", "M000001", "M000009", "M111111", "M999999"}
# This file necessarily talks ABOUT ids; it must not be its own counterexample.
SELF = os.path.abspath(__file__)
SCAN_EXT = (".py", ".md", ".yaml", ".yml", ".json", ".txt", ".sh")
SKIP_DIR = {".git", "__pycache__", "node_modules", ".venv", "venv", ".github"}


def _candidate_files():
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR]
        for n in names:
            path = os.path.join(root, n)
            if n.endswith(SCAN_EXT) and os.path.abspath(path) != SELF:
                yield path


def test_no_real_user_id_is_committed():
    found = []
    for path in _candidate_files():
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        for m in MID.finditer(text):
            if m.group(0) in SYNTHETIC:
                continue
            line = text[:m.start()].count("\n") + 1
            found.append(f"{os.path.relpath(path, REPO)}:{line}  {m.group(0)}")
    assert not found, (
        "real user ids in a PUBLIC repo — keep the finding, drop the who:\n  "
        + "\n  ".join(sorted(found)[:40])
        + (f"\n  … and {len(found) - 40} more" if len(found) > 40 else ""))


def test_a_user_id_is_never_written_next_to_a_name():
    """The 70-file comment paired a first name with an id inside one parenthetical. An id beside a human name is
    worse than either alone, so this stays a separate assertion even once the one above is green."""
    pair = re.compile(r"\b[A-Z][a-z]{2,}\b[^\n]{0,20}\bM\d{6}\b|\bM\d{6}\b[^\n]{0,20}\b[A-Z][a-z]{2,}\b")
    found = []
    for path in _candidate_files():
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        for m in pair.finditer(text):
            hit = MID.search(m.group(0))
            if hit and hit.group(0) in SYNTHETIC:
                continue
            line = text[:m.start()].count("\n") + 1
            found.append(f"{os.path.relpath(path, REPO)}:{line}  {m.group(0).strip()[:70]}")
    assert not found, "a user id next to a name:\n  " + "\n  ".join(sorted(found)[:20])
