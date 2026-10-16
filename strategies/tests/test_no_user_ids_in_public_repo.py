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
import subprocess

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MID = re.compile(r"\bM\d{6}\b")
# Placeholders. Deliberately unmistakable, and used as test data rather than as evidence.
SYNTHETIC = {"M123456", "M000001", "M000009", "M111111", "M999999"}
# This file necessarily talks ABOUT ids; it must not be its own counterexample.
SELF = os.path.abspath(__file__)
SKIP_DIR = {".git", "__pycache__", "node_modules", ".venv", "venv"}
# A file larger than this is data, not somewhere an id gets pasted. Above the largest tracked file
# (a ~4.6MB quant-desk fixture) so nothing scanned today drops out.
MAX_BYTES = 16_000_000


def _walked_files():
    """Fallback for a checkout with no usable git. Deliberately the WIDER of the two listings: a
    security test may end up scanning files it need not, never the reverse."""
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR]
        for n in names:
            yield os.path.join(root, n)


def _candidate_files():
    """Tracked files only. This test asks what is COMMITTED to a public repo, and `git ls-files` is
    the authoritative answer to that; walking the filesystem was only ever an approximation of it.

    The approximation broke on gitignored scratch checkouts — a local `git worktree` under .claude/
    is an entire second copy of this repo, so the walk re-scanned every file under a second name and
    reported hits at paths that are not in the repo at all. A suite that is red on every developer's
    machine is a suite nobody reads, which is precisely how a real leak would get waved through.

    Coverage of tracked files is unchanged (verified: zero tracked paths drop out). It only widens:
    every SKIP_DIR entry is untracked by definition, so git excludes those for free, while .github —
    committed and public — is scanned.
    """
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO,
            check=True, capture_output=True, text=True).stdout
        paths = (os.path.join(REPO, rel) for rel in listed.split("\0") if rel)
    except (OSError, subprocess.CalledProcessError):
        paths = _walked_files()
    for path in paths:
        if os.path.abspath(path) != SELF and os.path.isfile(path):
            yield path


def _read_text(path):
    """The file's text, or None for what cannot carry a pasted id: binaries (a NUL byte, or not
    UTF-8) and oversize files. Skipping by content, not by an ALLOW-list of extensions — that list
    silently passed every type nobody thought of (.ts, .toml, .env, a Dockerfile, LICENSE)."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as f:
            data = f.read()
        return None if b"\0" in data else data.decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _find_ids(paths, base=REPO):
    """Every non-synthetic id in `paths` — takes the paths so the guard's coverage can be tested on
    planted files; the real test feeds it the tracked files."""
    found = []
    for path in paths:
        text = _read_text(path)
        for m in MID.finditer(text or ""):
            if m.group(0) in SYNTHETIC:
                continue
            line = text[:m.start()].count("\n") + 1
            found.append(f"{os.path.relpath(path, base)}:{line}  {m.group(0)}")
    return found


def test_no_real_user_id_is_committed():
    found = _find_ids(_candidate_files())
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
        text = _read_text(path) or ""
        for m in pair.finditer(text):
            hit = MID.search(m.group(0))
            if hit and hit.group(0) in SYNTHETIC:
                continue
            line = text[:m.start()].count("\n") + 1
            found.append(f"{os.path.relpath(path, REPO)}:{line}  {m.group(0).strip()[:70]}")
    assert not found, "a user id next to a name:\n  " + "\n  ".join(sorted(found)[:20])


# ── the guard's own coverage ── an id was planted in `.github/` and in an unlisted extension, and the
# guard stayed green. These keep that from narrowing again.

def test_the_listing_reaches_github_and_files_without_a_listed_extension():
    listed = {os.path.relpath(p, REPO) for p in _candidate_files()}
    for rel in (".github/workflows/tests.yml", "LICENSE"):
        assert rel in listed, f"{rel} is tracked but the guard never reads it"


def test_an_id_in_any_text_file_is_found(tmp_path):
    for rel in (".github/workflows/probe.yml", "probe.ts", "probe.js", "probe.toml", "probe.cfg",
                "probe.env", "probe.csv", "probe.sql", "probe.tf", "Dockerfile"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("debug for M000000\n", encoding="utf-8")
        assert _find_ids([str(f)], str(tmp_path)), rel


def test_a_synthetic_id_is_still_allowed(tmp_path):
    f = tmp_path / "probe.yml"
    f.write_text("uses M123456 as a fixture\n", encoding="utf-8")
    assert _find_ids([str(f)], str(tmp_path)) == []


def test_binary_files_are_not_read(tmp_path):
    for name, data in (("logo.png", b"\x89PNG\r\n\x1a\n M000000"), ("blob.bin", b"\0\0 M000000")):
        (tmp_path / name).write_bytes(data)
        assert _find_ids([str(tmp_path / name)], str(tmp_path)) == [], name
