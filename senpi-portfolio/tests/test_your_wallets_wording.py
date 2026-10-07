#!/usr/bin/env python3
"""Your wallets — the wording gate (External Wallets amendment A1, invariant I1).

A wallet the user added in Your wallets is their claim, not proof of control. No text an agent reads —
an engine's printed strings, SKILL.md, a reference, a README row — may call it connected, verified,
owned or proven, and every pointer to the panel is the one phrase: "add it in Your wallets on senpi.ai
(web)". The skills install standalone; this test only reads their files by path.

    python3 -m pytest senpi-portfolio/tests/test_your_wallets_wording.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
POINTER = "add it in Your wallets on senpi.ai (web)"

# Every file here is read whole. None of them said "connect" before R1, so the word is banned outright;
# "connection" (a socket) is a different word and stays legal.
WHOLE_FILES = (
    "senpi-portfolio/scripts/portfolio.py",
    "senpi-improve-trades/scripts/review.py",
    "quant-desk/scripts/addresses.py",
    "quant-desk/scripts/desk.py",
    "senpi-portfolio/SKILL.md",
)
BANNED = (
    (r"(?i)connect(?!ion)", "connect(ed)"),
    (r"verified_at", "verified_at (the MCP sends added_at)"),
    (r"NOT_A_CONNECTED_WALLET", "NOT_A_CONNECTED_WALLET (the MCP sends NOT_A_SAVED_WALLET)"),
    (r"(?i)\bproved (they|you) own\b|\bone signature\b|\bwhat senpi can prove\b|\bownership is proven\b",
     "a proof-of-control claim"),
    (r"(?<!Your )Wallets on senpi\.ai", "the panel's old name (it is Your wallets)"),
)


def _text(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return " ".join(f.read().split())


def test_no_agent_facing_file_calls_a_saved_wallet_connected_or_proven():
    bad = []
    for rel in WHOLE_FILES:
        text = _text(rel)
        for pat, what in BANNED:
            for m in re.finditer(pat, text):
                bad.append(f"{rel}: {what}: …{text[max(0, m.start() - 50):m.end() + 30]}…")
    assert not bad, "\n".join(bad)


def test_every_panel_pointer_is_the_one_phrase():
    bad = []
    for rel in WHOLE_FILES:
        text = _text(rel)
        for m in re.finditer(r"on senpi\.ai \(web\)", text):
            if text[max(0, m.end() - len(POINTER)):m.end()] != POINTER:
                bad.append(f"{rel}: …{text[max(0, m.start() - 60):m.end()]}")
    assert not bad, "\n".join(bad)


# The Your-wallets sections of the skills: (file, start text, end text or None = the next heading).
# "verified" is legal elsewhere in some of these files (a runtime's liveness, Senpi's own issued
# wallets) but never where a saved wallet is described.
SECTIONS = (
    ("senpi-portfolio/SKILL.md", "## Your wallets (read-only)", None),
)
SECTION_BANNED = r"(?i)\bverified\b|\bproo?f\b|\bprov(e|ed|en)\b|\bsignature\b|\b(you|they) own\b|\bowned by\b|\bowning\b"


def _section(rel, start, end):
    text = _text(rel)
    assert start in text, f"{rel}: {start!r} not found"
    rest = text.split(start, 1)[1]
    if end is None:
        return re.split(r" #{2,3} ", rest, 1)[0]
    assert end in rest, f"{rel}: {end!r} not found after {start!r}"
    return rest.split(end, 1)[0]


def test_the_your_wallets_sections_never_say_verified_owned_or_proven():
    bad = []
    for rel, start, end in SECTIONS:
        sec = _section(rel, start, end)
        for m in re.finditer(SECTION_BANNED, sec):
            bad.append(f"{rel} {start}: …{sec[max(0, m.start() - 50):m.end() + 30]}…")
    assert not bad, "\n".join(bad)
