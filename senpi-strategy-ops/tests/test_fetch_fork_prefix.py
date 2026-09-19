"""Reinstalling a closed strategy has to resolve the user's FORK name to a template.

A deployed package is normally `<username>-<template>` (Make It Yours). That fork lives only on the
user's box — the repo carries `strategies/<template>/` and nothing else. So after the local copy is
gone (closed, or deleted by hand), `reinstall <username>-athena` looks like a package that does not
exist.

Two things this has to get right, both found in review rather than by me:

1. **The files land under the id the CALLER asked for.** `deploy.py` and `close.py` each rebuild
   `<dest_root>/<id>` from the id they passed. Writing the template's own name instead makes a
   successful download invisible to them — they report "fetched tree has no strategy.yaml", which
   is false and points away from the cause. That is a worse failure than not resolving at all.
2. **The template is found by SUFFIX, not by splitting on the first hyphen.** A Senpi username may
   contain hyphens, and `athena-x` is a template id with one — so the split point cannot be guessed.

Run: python3 -m pytest senpi-strategy-ops/tests/test_fetch_fork_prefix.py -q
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import _fetch  # noqa: E402

TREE = {"tree": [
    {"type": "blob", "path": "strategies/athena/strategy.yaml"},
    {"type": "blob", "path": "strategies/athena/phalanx/runtime.yaml"},
    {"type": "blob", "path": "strategies/athena-x/strategy.yaml"},
    {"type": "blob", "path": "strategies/athena-x/phalanx/runtime.yaml"},
    {"type": "blob", "path": "strategies/zec/strategy.yaml"},
]}


@pytest.fixture
def remote(monkeypatch):
    def _get(host, path, accept, timeout):
        if host == "api.github.com":
            return 200, json.dumps(TREE).encode()
        return 200, b"x: 1\n"
    monkeypatch.setattr(_fetch, "_get", _get)


def test_a_fork_name_fetches_the_template_under_the_fork_name(tmp_path, remote):
    """The substitution happens in the CONTENT, never in the path the caller gets back."""
    dest = _fetch.fetch_package("ignasteamsenpi-athena", tmp_path)
    assert dest == tmp_path / "ignasteamsenpi-athena"
    assert (dest / "strategy.yaml").exists()
    assert (dest / "phalanx" / "runtime.yaml").exists()
    assert not (tmp_path / "athena").exists()          # never under the template's own name


def test_a_hyphenated_username_still_resolves(tmp_path, remote):
    """`split("-", 1)` would give 'doe-athena' here and fail."""
    dest = _fetch.fetch_package("john-doe-athena", tmp_path)
    assert dest == tmp_path / "john-doe-athena"
    assert (dest / "strategy.yaml").exists()


def test_a_hyphenated_template_is_not_shadowed_by_a_shorter_one(tmp_path, remote):
    """`athena-x` ends with `-athena`... no it does not, but `user-athena-x` ends with BOTH
    `-athena-x` and `-x`-free `athena`-adjacent forms. Longest match wins, so the x variant
    resolves to itself rather than to plain athena."""
    dest = _fetch.fetch_package("purplefrog-athena-x", tmp_path)
    assert dest == tmp_path / "purplefrog-athena-x"
    assert (dest / "phalanx" / "runtime.yaml").exists()


def test_an_exact_template_id_is_untouched(tmp_path, remote):
    assert _fetch.fetch_package("athena", tmp_path) == tmp_path / "athena"


def test_a_hyphenated_id_that_is_not_a_fork_still_fails(tmp_path, remote):
    """`zec-fade` has no `strategies/fade/` — it must not silently fetch something else."""
    with pytest.raises(_fetch.FetchError) as e:
        _fetch.fetch_package("zec-fade", tmp_path)
    assert "zec-fade" in str(e.value) and "username" in str(e.value)


def test_the_refusal_never_points_at_github_as_an_owner(tmp_path, remote):
    with pytest.raises(_fetch.FetchError) as e:
        _fetch.fetch_package("nobody-nothing", tmp_path)
    msg = str(e.value)
    assert "<username>-<template>" in msg and "Senpi username" in msg


def test_the_substitution_is_announced(tmp_path, remote, capsys):
    """The fork is the user's own copy. Handing back the pristine template under that name without
    saying so means their tuning is quietly not there."""
    _fetch.fetch_package("ignasteamsenpi-athena", tmp_path)
    err = capsys.readouterr().err
    assert "athena" in err and "NOT in this copy" in err


def test_an_exact_match_says_nothing(tmp_path, remote, capsys):
    _fetch.fetch_package("athena", tmp_path)
    assert capsys.readouterr().err == ""
