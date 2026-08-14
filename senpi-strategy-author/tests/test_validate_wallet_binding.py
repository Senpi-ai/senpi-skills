#!/usr/bin/env python3
"""`wallet_env` is a promise the manifest makes on behalf of the recipe, and only `strategy.wallet`
says whether the recipe keeps it.

Asking the question of the file's TEXT instead of its parsed `strategy.wallet` is answerable by a
token sitting anywhere at all — a `note:`, a comment, an input nothing reads. The render then
substitutes that stray token harmlessly and leaves no `${...}` behind, so the unresolved-placeholder
check clears it too, and a recipe pinning a hardcoded address sails through. Deploy would create and
fund a fresh wallet from the user's budget and install the strategy, exit engine included, against
the pinned one.

The runtime refuses exactly that (`E_VALIDATE_WALLET_UNBOUND`, off the parsed field). This rule lives
here as well so author-green == deploy-green: a package this validator passes must not be refused at
deploy.

    python3 -m pytest senpi-strategy-author/tests/
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import validate_strategy as vs  # noqa: E402

_RUNTIME = """\
name: {sid}-main
group: {sid}
description: >
  A deliberately ordinary fixture recipe whose only variable is the wallet binding, so the
  binding rule is the one thing under test here and nothing else can colour the result.
strategy:
  wallet: {wallet}
  slots: 1
  margin_pct: 20
exit:
  dsl_preset: let_winners_run
scanners:
  - type: external_scanner
    path: ./scanners
    entrypoint: scan.py
    interval_seconds: 900
    inputs: {{}}
    signal_data_schema:
      score:
        type: float
{extra}"""


def _package(tmp_path, sid="pinned", wallet='"${PINNED_WALLET}"', extra=""):
    """A DECLARED-instance package — the layout every shipped strategy uses, and the one where the
    manifest, not the recipe, names the wallet env."""
    d = tmp_path / sid
    (d / "main" / "scanners").mkdir(parents=True)
    (d / "strategy.yaml").write_text(
        f'id: {sid}\nversion: "1.0.0"\ninstances:\n'
        f"  - name: main\n    runtime: main/runtime.yaml\n"
        f"    wallet_env: PINNED_WALLET\n    funding_share: 1.0\n"
    )
    (d / "main" / "runtime.yaml").write_text(_RUNTIME.format(sid=sid, wallet=wallet, extra=extra))
    (d / "main" / "scanners" / "scan.py").write_text("def scan(inputs, ctx):\n    return []\n")
    (d / "main" / "scanners" / "scoring.py").write_text("def score(x):\n    return x\n")
    return d


def _binding_errors(errs):
    return [e for e in errs if "PINNED_WALLET" in e and "strategy.wallet" in e]


def test_a_pinned_wallet_is_refused_even_when_the_token_appears_elsewhere():
    """The evasion: the declared token is present in the file, just not where it decides anything."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        d = _package(
            Path(tmp),
            wallet='"0x%s"' % ("a" * 40),
            extra='note: "funded via ${PINNED_WALLET}"\n',
        )
        assert _binding_errors(vs.validate(Path(d))), vs.validate(Path(d))


def test_a_correctly_bound_recipe_passes(tmp_path):
    """The guard against over-refusing — this is the shape all 103 shipped packages use."""
    assert _binding_errors(vs.validate(Path(_package(tmp_path)))) == []


def test_a_recipe_with_no_strategy_wallet_is_refused(tmp_path):
    """Nothing to substitute into is the same fault as substituting into the wrong place."""
    d = _package(tmp_path)
    rt = d / "main" / "runtime.yaml"
    rt.write_text(rt.read_text().replace('  wallet: "${PINNED_WALLET}"\n', ""))
    assert _binding_errors(vs.validate(Path(d)))
