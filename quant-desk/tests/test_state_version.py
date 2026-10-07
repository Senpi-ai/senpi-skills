"""A cached desk run is reused only when the SAME desk version wrote it. A run cached before 1.42
was judged under the old protection rule; re-rendering it under the new one would print the old
verdict with the new version in the header."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import desk  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "sample_trader.json")
SENTINEL = "SENTINEL — this verdict came from the cache"


def _addr():
    with open(FIXTURE) as fh:
        return json.load(fh)["address"].lower()


def _run(tmp_path, capsys, *extra):
    assert desk.main([_addr(), "--fixture", FIXTURE, "--dry", "--state-dir", str(tmp_path), "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def _poison(tmp_path, version):
    """Rewrite the cached run (fresh mtime) with a sentinel verdict, stamped `version` (None = unstamped)."""
    sp = tmp_path / f"desk-{_addr()}.json"
    r = json.loads(sp.read_text())
    r["verdict"] = SENTINEL
    if version is None:
        r.pop("desk_version", None)
    else:
        r["desk_version"] = version
    sp.write_text(json.dumps(r))
    return sp


def test_the_saved_run_is_stamped_with_the_desk_version(tmp_path, capsys):
    _run(tmp_path, capsys)
    assert json.loads((tmp_path / f"desk-{_addr()}.json").read_text())["desk_version"] == desk.VERSION


def test_a_section_reuses_a_cached_run_of_the_same_version(tmp_path, capsys):
    _run(tmp_path, capsys)
    _poison(tmp_path, desk.VERSION)
    assert _run(tmp_path, capsys, "--section", "protection")["verdict"] == SENTINEL


def test_a_section_never_reuses_a_run_cached_by_another_version(tmp_path, capsys):
    for stale in ("1.40.0", "1.41.0", None):
        _run(tmp_path, capsys)
        _poison(tmp_path, stale)
        r = _run(tmp_path, capsys, "--section", "protection")
        assert r["verdict"] != SENTINEL and r["desk_version"] == desk.VERSION, stale


def test_a_deep_dive_never_reuses_a_run_cached_by_another_version(tmp_path, capsys):
    _run(tmp_path, capsys)
    _poison(tmp_path, "1.40.0")
    _run(tmp_path, capsys, "--deep", "watch")
    assert json.loads((tmp_path / f"desk-{_addr()}.json").read_text())["verdict"] != SENTINEL


def test_compare_never_reuses_a_run_cached_by_another_version(tmp_path, capsys):
    _run(tmp_path, capsys)
    sp = _poison(tmp_path, "1.40.0")
    a = _addr()
    assert desk.main(["--compare", a, a, "--fixture", FIXTURE, "--dry", "--state-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert json.loads(sp.read_text())["verdict"] != SENTINEL
