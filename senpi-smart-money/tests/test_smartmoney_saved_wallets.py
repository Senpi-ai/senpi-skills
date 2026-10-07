#!/usr/bin/env python3
"""The smart-money alignment check includes the wallets the user added (External Wallets R1, amendment A1).

CTA 1 ("check how our positions align with where smart money is moving") reads one book: the Senpi strategies AND
the wallets the user added in Your wallets (their claim, never checked; read-only to Senpi). One table,
every row labelled managed or read-only, no section per origin. A saved wallet may be said to sit with or
against the proven cohort; any follow-up offered is a Senpi-side action, never one on the saved wallet.
Unknown is never zero, and `protection` (live exchange stops on a saved wallet) is never merged with
"protected" (a Senpi runtime exit).

    python3 -m pytest senpi-smart-money/tests/test_smartmoney_saved_wallets.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = re.sub(r"\s+", " ", (ROOT / "senpi-smart-money" / "SKILL.md").read_text(encoding="utf-8"))
START = "- **CTA 1 → positions read: the Senpi strategies plus the wallets the user added.**"
END = "- **CTA 2 → strategy.**"
COHORT = "with or against the proven cohort"


def _section():
    assert START in SKILL, START
    rest = SKILL.split(START, 1)[1]
    assert END in rest, END
    return rest.split(END, 1)[0]


def test_the_positions_read_includes_the_saved_wallets_in_one_labelled_table():
    sec = _section()
    for needle in ("`account_get_external_wallets` (no address",
                   "**same table** as the Senpi strategies",
                   "never a section per origin",
                   "largest position value first",
                   "`Senpi strategy <name> (managed)`",
                   "`your wallet <label> (read-only)`",
                   "never recompute them",
                   "Hyperliquid main and xyz dexes only"):
        assert needle in sec, needle


def test_a_saved_wallet_is_read_only_and_any_action_offered_is_senpi_side():
    sec = _section()
    for needle in ("Senpi can't place, change or cancel orders on a saved wallet",
                   "`access` line",
                   COHORT,
                   "any action you offer is a Senpi-side one (a Senpi strategy)",
                   "never a trade, stop, close or strategy on the saved wallet"):
        assert needle in sec, needle


def test_protection_is_never_merged_with_protected():
    sec = _section()
    assert '`protection` is not "protected".' in sec
    assert "Never merge them." in sec


def test_unknown_is_never_zero():
    sec = _section()
    for needle in ("I couldn't load your saved wallets", 'never "you have no saved wallets"',
                   "`state: null`", "`state.readError`", 'never flat, never $0, never "no positions"'):
        assert needle in sec, needle


def test_the_section_never_calls_a_saved_wallet_connected_verified_or_owned():
    sec = _section()
    bad = re.findall(r"(?i)connect(?!ion)|\bverified\b|\bproo?f\b|\bprov(?:e|ed|en)\b(?! cohort)|"
                     r"\b(?:you|they) own\b|\bowned by\b|\bowning\b", sec)
    assert not bad, bad
    assert '"your wallets" or "the wallets you added"' in sec
    assert "never imply Senpi checked who controls them" in sec
