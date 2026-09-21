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
# An ALLOW-list of extensions is the wrong shape for this: it silently passes every file type
# nobody thought of. Both blind spots were demonstrated by planting an id and watching the guard
# stay green — one in `.github/` (which was skipped outright) and one in an unlisted extension.
# So: skip only what cannot carry readable text, and read everything else that decodes.
#
# `.github` is NOT skipped. Workflow files are text, they are public, and they are exactly the kind
# of place a debugging id gets pasted and forgotten.
SKIP_DIR = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", "dist", "build"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tgz",
              ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4", ".mov", ".mp3", ".wav",
              ".pyc", ".pyo", ".so", ".dylib", ".dll", ".wasm", ".jar", ".class"}
MAX_BYTES = 4_000_000      # a file larger than this is data, not somewhere an id gets pasted


def _candidate_files(base=None):
    for root, dirs, names in os.walk(base or REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR]
        for n in names:
            path = os.path.join(root, n)
            if os.path.splitext(n)[1].lower() in BINARY_EXT:
                continue
            if os.path.abspath(path) == SELF:
                continue
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
            except OSError:
                continue
            yield path


def _find_ids(base=None):
    """Every non-synthetic id under `base`. Takes a root so the guard's own coverage is testable
    against a planted tree rather than by trusting the extension list."""
    found = []
    for path in _candidate_files(base):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (UnicodeDecodeError, OSError):
            continue
        for m in MID.finditer(text):
            if m.group(0) in SYNTHETIC:
                continue
            line = text[:m.start()].count("\n") + 1
            found.append(f"{os.path.relpath(path, base or REPO)}:{line}  {m.group(0)}")
    return found


def test_no_real_user_id_is_committed():
    found = _find_ids()
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


# ── the guard's own coverage ──
#
# Both of these were demonstrated live: an id was planted and the guard stayed green. An ALLOW-list
# of extensions passes every file type nobody thought of, and `.github` was skipped outright — which
# is precisely where a debugging id gets pasted into a workflow and forgotten. These plant the same
# two and require the guard to bite, so the coverage cannot silently narrow again.

def _plant(tmp_path, relpath):
    f = tmp_path / relpath
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("debug for M408027\n", encoding="utf-8")
    return _find_ids(str(tmp_path))


def test_an_id_in_a_github_workflow_is_found(tmp_path):
    assert _plant(tmp_path, ".github/workflows/probe.yml")


def test_an_id_in_an_unlisted_extension_is_found(tmp_path):
    for ext in (".ts", ".js", ".toml", ".cfg", ".env", ".csv", ".sql", ".tf"):
        assert _plant(tmp_path, f"probe{ext}"), ext


def test_an_id_in_a_file_with_no_extension_is_found(tmp_path):
    assert _plant(tmp_path, "Dockerfile")


def test_a_synthetic_id_is_still_allowed_anywhere(tmp_path):
    f = tmp_path / ".github" / "workflows" / "probe.yml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("uses M123456 as a fixture\n", encoding="utf-8")
    assert _find_ids(str(tmp_path)) == []


def test_binary_files_are_not_read(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"M408027")
    assert _find_ids(str(tmp_path)) == []
