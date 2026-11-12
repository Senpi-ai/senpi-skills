#!/usr/bin/env python3
"""A saved wallet is never a copy candidate (External Wallets R1, amendment A1).

A saved wallet is one the user added in Your wallets by pasting its address — their claim, not proof of
control — and trades by hand. Asked about one, this skill routes it to `quant-desk` (score, leaks,
protection) or `senpi-improve-trades` (review my trades) instead of vetting it as someone to mirror, and
the find path never puts one on the copy shortlist.

"Is this a saved wallet" is decided by the engine against `user_get_me.external_wallets` (case-
insensitive), never guessed. A non-saved address keeps today's vet. An unreadable saved-wallets list is
"unavailable", never "none" — the vet still runs, and the engine says it could not check.

    python3 -m pytest senpi-trader-research/tests/test_research_saved_wallets.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import research  # noqa: E402

SKILL = os.path.join(HERE, "..", "SKILL.md")
ACCESS = "Read-only. Senpi can analyze this wallet. It cannot place, change or cancel orders on it."
SAVED = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20


def _me(status="ok", wallets=((SAVED, "MetaMask"),)):
    user = {"wallets": [], "external_wallets_status": status}
    if status == "ok":
        user["external_wallets"] = [{"address": a, "label": lbl, "added_at": "2026-10-01T15:31:02.000Z",
                                     "access": ACCESS} for a, lbl in wallets]
    return {"success": True, "data": {"user": user}}


class _Client:
    """`me` is the user_get_me reply (or an Exception to raise); discovery returns two traders, one of them
    the saved wallet. Every call is recorded."""
    def __init__(self, me):
        self.me, self.calls = me, []

    def mcp_call(self, tool, timeout=12, **kw):
        self.calls.append(tool)
        if tool == "user_get_me":
            if isinstance(self.me, Exception):
                raise self.me
            return self.me
        if tool == "discovery_get_top_traders":
            return {"success": True, "data": {"traders": [
                {"address": SAVED, "roi": 120.0, "pnl": 50000.0, "winRate": 60.0, "maxDrawdown": -20.0},
                {"address": OTHER, "roi": 80.0, "pnl": 30000.0, "winRate": 55.0, "maxDrawdown": -25.0}]}}
        return None


# ── vet ────────────────────────────────────────────────────────────────────────────────────────
def test_a_saved_wallet_is_routed_to_the_desk_never_vetted():
    c = _Client(_me())
    res = research.run(c, "vet", addr=SAVED.upper().replace("0X", "0x"))   # any case matches
    sw = res["saved_wallet"]
    assert res["trader"] is None, "a saved wallet must not get a copy dossier"
    assert sw["route"] == "quant-desk" and sw["review_route"] == "senpi-improve-trades"
    assert sw["label"] == "MetaMask" and sw["access"] == ACCESS
    assert "one of the wallets you added in Your wallets" in sw["say"]
    assert res["meta"]["saved_wallets_status"] == "ok"
    vet_tools = {"discovery_get_top_traders", "discovery_get_trader_history",
                 "discovery_get_trader_state", "leaderboard_get_trader"}
    assert not vet_tools & set(c.calls), f"vet calls ran on a saved wallet: {c.calls}"


def test_a_non_saved_address_keeps_todays_vet():
    c = _Client(_me())
    res = research.run(c, "vet", addr=OTHER)
    assert "saved_wallet" not in res
    assert res["trader"]["address"] == OTHER
    assert "discovery_get_trader_state" in c.calls


def test_an_unreadable_saved_list_is_unavailable_and_the_vet_still_runs():
    for me in (_me(status="unavailable"), {"success": True, "data": {"user": {"wallets": []}}},
               RuntimeError("user_get_me HTTP 503")):
        c = _Client(me)
        res = research.run(c, "vet", addr=SAVED)
        assert res["meta"]["saved_wallets_status"] == "unavailable", me
        assert res["trader"]["address"] == SAVED
        assert any("couldn't load the user's saved wallets" in w for w in res["meta"]["warnings"]), me


# ── find ───────────────────────────────────────────────────────────────────────────────────────
def test_the_find_path_never_shortlists_a_saved_wallet():
    res = research.run(_Client(_me()), "top", enrich_top=5)
    addrs = [c["address"] for c in res["candidates"]]
    assert SAVED not in addrs and OTHER in addrs
    assert SAVED not in [c["address"] for c in res.get("mirror_shortlist", [])]
    assert res["meta"]["saved_wallets_excluded"] == [{"short": research._short(SAVED), "label": "MetaMask"}]


def test_the_find_path_with_an_unreadable_list_keeps_every_candidate_and_says_so():
    res = research.run(_Client(_me(status="unavailable")), "top", enrich_top=5)
    assert SAVED in [c["address"] for c in res["candidates"]]
    assert res["meta"]["saved_wallets_status"] == "unavailable"
    assert "saved_wallets_excluded" not in res["meta"]


def test_the_strategies_ranking_does_not_read_saved_wallets():
    c = _Client(RuntimeError("must not be called"))
    research.run(c, "strategies", limit=3)
    assert "user_get_me" not in c.calls


# ── the vendored reader matches its homes ──────────────────────────────────────────────────────
_CW_BLOCK = re.compile(r"^# ── VENDORED external-wallets reader,.*?^# ── end external-wallets reader$", re.S | re.M)


def _block(path):
    with open(path, encoding="utf-8") as f:
        found = _CW_BLOCK.search(f.read())
    assert found, f"vendored saved-wallets reader not found in {path}"
    return hashlib.sha256(found.group(0).encode("utf-8")).hexdigest()


def test_the_saved_wallets_reader_is_byte_identical_to_portfolios():
    """Skills install standalone, so research.py carries a copy; it must answer 'which saved wallets?'
    exactly as senpi-portfolio does (an absent key is unavailable, never [])."""
    assert _block(os.path.join(HERE, "..", "scripts", "research.py")) == _block(
        os.path.join(ROOT, "senpi-portfolio", "scripts", "portfolio.py"))


# ── the SKILL.md surface ───────────────────────────────────────────────────────────────────────
def _section():
    with open(SKILL, encoding="utf-8") as f:
        text = " ".join(f.read().split())
    start = "**One of the user's saved wallets? Not a copy candidate.**"
    assert start in text
    return text.split(start, 1)[1].split("You are a sharp due-diligence analyst", 1)[0]


def test_skill_routes_a_saved_wallet_to_the_desk_and_leaves_mirroring_it_open():
    sec = _section()
    for needle in ("`saved_wallet`", "`quant-desk`", "`senpi-improve-trades`", "relay its `say` line",
                   "never on the copy shortlist", "never guess",
                   "don't call it illegitimate and don't run the vet",
                   "copying a wallet you added isn't set up here yet",
                   '`meta.saved_wallets_status: "unavailable"`',
                   "I couldn't load your saved wallets"):
        assert needle in sec, needle


def test_skill_section_never_calls_a_saved_wallet_connected_verified_or_owned():
    sec = _section()
    bad = re.findall(r"(?i)connect(?!ion)|\bverified\b|\bproo?f\b|\bprov(e|ed|en)\b|\b(you|they) own\b|"
                     r"\bowned by\b|\bowning\b", sec)
    assert not bad, bad
    for m in re.finditer(r"on senpi\.ai \(web\)", sec):
        assert sec[:m.end()].endswith("add it in Your wallets on senpi.ai (web)")


def test_the_engines_say_line_is_clean_too():
    say = research.SAVED_WALLET_SAY.format(short="0xabab…babab")
    assert not re.search(r"(?i)connect|verified|\bproo?f\b|\bproven\b|\byou own\b", say), say
