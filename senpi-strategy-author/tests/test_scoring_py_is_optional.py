"""`scoring.py` is optional, and the validator used to insist on it.

CLAUDE.md is explicit — "scoring.py  optional pure math (no I/O) so the edge unit-tests" — but the
check required the file unconditionally. That went unnoticed for as long as every package happened
to ship one; the first package whose scanner imports a vendored engine instead got a hard ✗ on a
structurally sound layout, from the very preflight the author SKILL tells people to run.

The question the validator should ask is not "is scoring.py present" but "does anything import
`scoring` without it being there".

Run: python3 -m pytest senpi-strategy-author/tests/test_scoring_py_is_optional.py -q
"""
import os
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, "..", "scripts", "validate_strategy.py")

RUNTIME = """\
name: probe-main
group: probe
version: 3.0.0
strategy:
  wallet: "${PROBE_WALLET}"
  slots: 1
scanners:
  - name: probe_scan
    type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 300
actions: []
"""
MANIFEST = """\
schema_version: 1
id: probe
version: "1.0.0"
catalog:
  name: "Probe"
  tagline: "t"
instances:
  - name: main
    runtime: main/runtime.yaml
    wallet_env: PROBE_WALLET
    funding_share: 1.0
"""


def _pkg(tmp_path, scan_body, with_scoring):
    d = tmp_path / "probe"
    (d / "main" / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(MANIFEST)
    (d / "main" / "runtime.yaml").write_text(RUNTIME)
    (d / "main" / "scanners" / "scan.py").write_text(textwrap.dedent(scan_body))
    if with_scoring:
        (d / "main" / "scanners" / "scoring.py").write_text("def f():\n    return 1\n")
    return d


def _run(pkg):
    r = subprocess.run([sys.executable, VALIDATOR, str(pkg)], capture_output=True, text=True)
    return r.stdout + r.stderr


def test_a_scanner_that_never_imports_scoring_does_not_need_it(tmp_path):
    out = _run(_pkg(tmp_path, "import sweep\n\ndef scan(inputs, ctx):\n    return []\n", False))
    assert "missing sibling" not in out and "imports `scoring`" not in out


def test_a_scanner_that_imports_scoring_still_needs_it(tmp_path):
    out = _run(_pkg(tmp_path, "import scoring\n\ndef scan(inputs, ctx):\n    return []\n", False))
    assert "imports `scoring`" in out


def test_from_import_counts_too(tmp_path):
    out = _run(_pkg(tmp_path, "from scoring import f\n\ndef scan(inputs, ctx):\n    return []\n", False))
    assert "imports `scoring`" in out


def test_a_mention_in_a_comment_or_string_is_not_an_import(tmp_path):
    body = '"""talks about scoring.py"""\n# import scoring would be wrong here\n\ndef scan(inputs, ctx):\n    return []\n'
    out = _run(_pkg(tmp_path, body, False))
    assert "imports `scoring`" not in out


def test_the_pair_together_is_fine(tmp_path):
    out = _run(_pkg(tmp_path, "import scoring\n\ndef scan(inputs, ctx):\n    return []\n", True))
    assert "imports `scoring`" not in out
