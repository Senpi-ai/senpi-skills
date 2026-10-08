#!/usr/bin/env python3
"""Your wallets — the wording gate (External Wallets amendment A1, invariant I1).

A wallet the user added in Your wallets is their claim, not proof of control. No text an agent reads —
an engine's printed strings, SKILL.md, a reference, a README row — may call it connected, verified,
owned or proven, and every pointer to the panel is one of two phrases: "add it in Your wallets on senpi.ai
(web)" or "remove it in Your wallets on senpi.ai (web)". The skills install standalone; this test only reads their files by path.

    python3 -m pytest senpi-portfolio/tests/test_your_wallets_wording.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
POINTER = "add it in Your wallets on senpi.ai (web)"
REMOVE_POINTER = "remove it in Your wallets on senpi.ai (web)"      # the only other permitted pointer

# Every file here is read whole. None of them said "connect" before R1, so the word is banned outright;
# "connection" (a socket) is a different word and stays legal.
WHOLE_FILES = (
    "senpi-portfolio/scripts/portfolio.py",
    "senpi-improve-trades/scripts/review.py",
    "quant-desk/scripts/addresses.py",
    "quant-desk/scripts/desk.py",
    "quant-desk/scripts/render.py",
    "senpi-trader-research/scripts/research.py",
    "senpi-strategy-discover/scripts/discover.py",
    "senpi-portfolio/SKILL.md",
    "senpi-improve-trades/SKILL.md",
    "senpi-improve-trades/references/output-shape.md",
    "README.md",
    "quant-desk/SKILL.md",
    "quant-desk/references/methodology.md",
    "senpi-trade/SKILL.md",
    "senpi-trader-research/SKILL.md",
    "senpi-market-pulse/SKILL.md",
    "senpi-smart-money/SKILL.md",
)
BANNED = (
    (r"(?i)connect(?!ion)", "connect(ed)"),
    (r"verified_at", "verified_at (the MCP sends added_at)"),
    (r"NOT_A_CONNECTED_WALLET", "NOT_A_CONNECTED_WALLET (the MCP sends NOT_A_SAVED_WALLET)"),
    (r"(?i)\bproved (they|you) own\b|\bone signature\b|\bwhat senpi can prove\b|\bownership is proven\b",
     "a proof-of-control claim"),
    (r"(?<!Your )Wallets on senpi\.ai", "the panel's old name (it is Your wallets)"),
    # a saved wallet is one the user added — never "your/their own saved wallet" (I1: Senpi can't confirm it)
    (r"(?i)\b(your|their) own saved wallets?\b", "an ownership claim on a saved wallet"),
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
            tail = text[max(0, m.end() - len(REMOVE_POINTER)):m.end()]
            if text[max(0, m.end() - len(POINTER)):m.end()] != POINTER and tail != REMOVE_POINTER:
                bad.append(f"{rel}: …{text[max(0, m.start() - 60):m.end()]}")
    assert not bad, "\n".join(bad)


def test_the_panel_is_never_named_without_a_permitted_pointer():
    """Any "Your wallets on senpi" must be one of the two pointers, whole — no "manage/edit/see them in …"."""
    bad = []
    for rel in WHOLE_FILES:
        text = _text(rel)
        for m in re.finditer(r"Your wallets on senpi", text):
            head = text[max(0, m.start() - 16):m.start()]
            tail = text[m.start():m.start() + len("Your wallets on senpi.ai (web)")]
            if not (re.search(r"(add|remove) it in $", head) and tail == "Your wallets on senpi.ai (web)"):
                bad.append(f"{rel}: …{text[max(0, m.start() - 40):m.end() + 20]}…")
    assert not bad, "\n".join(bad)


def test_removal_is_pointed_to_in_the_portfolio_and_quant_desk_sections():
    for rel, start, end in (("senpi-portfolio/SKILL.md", "## One wallet list — every wallet first-class", None),
                            ("quant-desk/SKILL.md", '**"My wallets" are', "**The desk remembers.**")):
        assert REMOVE_POINTER in _section(rel, start, end), rel


# The Your-wallets sections of the skills: (file, start text, end text or None = the next heading).
# "verified" is legal elsewhere in some of these files (a runtime's liveness, Senpi's own issued
# wallets) but never where a saved wallet is described.
SECTIONS = (
    ("senpi-portfolio/SKILL.md", "## One wallet list — every wallet first-class", None),
    ("senpi-improve-trades/SKILL.md", "## One book — every wallet first-class",
     "## Your wallets (read-only, traded by hand)"),
    ("senpi-improve-trades/SKILL.md", "## Your wallets (read-only, traded by hand)", None),
    ("quant-desk/SKILL.md", '**"My wallets" are', "**The desk remembers.**"),
    ("quant-desk/SKILL.md", "### If they mean their OWN book", None),
    ("senpi-trade/SKILL.md", "- **A request to act on one of the user's SAVED wallets**",
     "- **Finding / vetting the trader"),
    ("senpi-trader-research/SKILL.md", "**One of the user's saved wallets? Not a copy candidate.**",
     "You are a sharp due-diligence analyst"),
    ("senpi-market-pulse/SKILL.md", "- **Saved wallets in the same read.**",
     "never imply Senpi checked who controls them."),
    # senpi-strategy-discover/SKILL.md is not read whole ("interconnect" is legal there), only its paragraph.
    ("senpi-strategy-discover/SKILL.md", "It returns `holdings` (coins in their Senpi strategies) and "
     "`saved_wallet_holdings`", "never imply Senpi checked who controls them."),
    # senpi-smart-money/SKILL.md is read whole but has no section entry: its subject is the "proven
    # cohort", which SECTION_BANNED would read as a proof-of-control claim.
)
SECTION_BANNED = (r"(?i)\bverified\b|\bproo?f\b|\bprov(e|ed|en)\b|\bsignature\b|\b(you|they) own\b|\bowned by\b"
                  r"|\bowning\b|\b(your|their) own (saved )?wallets?\b")
# "their own book" is how quant-desk names the reader's-own-book path (a pasted address they said is theirs);
# in the sections that describe a SAVED wallet it is an ownership claim, so it is banned there only.
OWN_BOOK_BANNED_IN = ("senpi-trader-research/SKILL.md", "senpi-improve-trades/SKILL.md", "senpi-portfolio/SKILL.md",
                      "senpi-trade/SKILL.md", "senpi-market-pulse/SKILL.md", "senpi-strategy-discover/SKILL.md")


def _section(rel, start, end):
    text = _text(rel)
    assert start in text, f"{rel}: {start!r} not found"
    rest = text.split(start, 1)[1]
    if end is None:
        return re.split(r" #{2,3} ", rest, maxsplit=1)[0]
    assert end in rest, f"{rel}: {end!r} not found after {start!r}"
    return rest.split(end, 1)[0]


def test_the_your_wallets_sections_never_say_verified_owned_or_proven():
    bad = []
    for rel, start, end in SECTIONS:
        sec = _section(rel, start, end)
        for m in re.finditer(SECTION_BANNED, sec):
            bad.append(f"{rel} {start}: …{sec[max(0, m.start() - 50):m.end() + 30]}…")
        if rel in OWN_BOOK_BANNED_IN:
            for m in re.finditer(r"(?i)\b(your|their) own book\b", sec):
                bad.append(f"{rel} {start}: own book: …{sec[max(0, m.start() - 50):m.end() + 30]}…")
    assert not bad, "\n".join(bad)


def test_the_saved_wallet_route_line_presumes_no_ownership():
    """trader-research's `say` for a saved wallet is relayed verbatim — it says "a wallet you added", never
    "your own wallet / your book" (the `control` line: Senpi can't confirm it is theirs)."""
    text = _text("senpi-trader-research/scripts/research.py")
    say = text.split("SAVED_WALLET_SAY = (", 1)[1].split(")", 1)[0]
    assert "one of the wallets you added" in say
    assert not re.search(r"(?i)\bown\b|your book", say), say
