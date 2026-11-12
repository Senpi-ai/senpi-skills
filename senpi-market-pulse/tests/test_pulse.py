#!/usr/bin/env python3
"""Offline engine test — runs pulse.run() against a recorded MCP fixture (no network).

    python3 -m pytest senpi-market-pulse/tests/        # or: python3 tests/test_pulse.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)

import pulse  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "pulse_fixture.json")


def _client():
    with open(FIXTURE) as f:
        return pulse._FixtureClient(json.load(f))


def _result():
    return pulse.run(_client(), want_smart=True)


def _state_path():
    """A fresh state-file path in a throwaway temp dir (the file itself does not exist yet)."""
    return os.path.join(tempfile.mkdtemp(prefix="pulse-test-"), "state.json")


def test_all_classes_present():
    """Every asset class is always returned — never crypto-only."""
    g = _result()["groups"]
    for required in ("crypto", "semis_memory", "software_megacap", "indices", "commodities", "macro_fx"):
        assert required in g, f"missing group {required}"
    assert g["crypto"]["avg_change_pct"] is not None
    assert g["crypto"]["avg_change_pct"] < 0  # the fixture is a down day for crypto


def test_context_nested_quotes_extracted():
    """Regression: live market_list_instruments nests markPx/prevDayPx under `context`.
    The fixture uses that real shape; prices must still come through (not all-null)."""
    g = _result()["groups"]
    btc = next(r for r in g["crypto"]["rows"] if r["asset"] == "BTC")
    assert btc["price"] == 62515 and btc["change_pct"] is not None
    # xyz rows arrive `xyz:`-prefixed — the symbol must be normalized and matched
    sp500 = next(r for r in g["indices"]["rows"] if r["asset"] == "SP500")
    assert sp500["price"] == 7396


def test_day_classified_risk_off():
    sig = _result()["signals"]
    assert sig["day_classification"]["label"] == "risk_off"
    assert sig["day_classification"]["groups_down"] >= 3


def test_dispersion_read():
    """SP500 calm while memory names break → the engine should flag dispersion, not capitulation."""
    sig = _result()["signals"]
    disp = sig["dispersion"]
    assert disp["worst_group"] in ("semis_memory", "semis_equipment")
    assert "dispersion" in (disp["read"] or "")


def test_confirmation_checklist():
    sig = _result()["signals"]
    assert "haven bid intact" in (sig["gold"]["read"] or "")     # gold only -1.2%
    assert "no funding stress" in (sig["dxy"]["read"] or "")     # DXY flat
    assert "contained" in (sig["vix"]["read"] or "")             # VIX 20 < 22


def test_movers_get_volume():
    """The biggest movers should be deep-pulled and carry volume conviction."""
    groups = _result()["groups"]
    rows = [r for g in groups.values() for r in g["rows"]]
    assert any(r.get("volume_usd") for r in rows), "no mover got a volume read"


def test_smart_money_present_when_healthy():
    res = _result()
    assert res["meta"]["smart_money_available"] is True
    sm = res["smart_money"]
    assert sm["status"] == {"window": "4h", "last_update_timestamp": 1782226800000,
                            "total_leaderboard_traders": 4821}
    hype = sm["concentration"]["rows"][0]
    assert (hype["token"], hype["direction"], hype["trader_count"]) == ("HYPE", "short", 228)
    assert sm["top_traders"]["rows"][0] == {"rank": 1, "trader_id": "0xabc", "unrealized_pnl": 1200000,
                                            "pnl_percentage": 38.5, "top_markets": ["HYPE", "BTC"]}
    assert sm["top_traders"]["total_traders"] == 4821
    # 0xabc's newest event is a positionless blocked repeat: its older sent event is the one narrated;
    # 0x123's blocked event stays — blocking only stops the push
    events = sm["momentum_events"]["rows"]
    assert [(e["trader_id"], e["decision"]) for e in events] == [("0xabc", "sent"), ("0x123", "blocked")]
    assert events[1]["blocked_reason"] == "system_cooldown_active" and "blocked_reason" not in events[0]


# Shapes per senpi-hyperliquid-mcp src/types/leaderboard.types.ts + src/tools/leaderboard.tools.ts: each
# handler wraps its client payload under one more key (data.leaderboard.data[], data.events.events[], …).
def _live_sized_client():
    """Live-sized reads: the full 200-trader board, a status carrying a price for every market, and the
    momentum feed as it reads live — 50 events, nearly all cooldown-blocked repeats of two traders."""
    def event(i, trader, decision, positions):
        return {"trader_id": trader, "tier": 1, "tier_label": "Exceptional", "delta_pnl": 2_000_000 + i,
                "decision": decision, "blocked_reason": None if decision == "sent" else "trader_cooldown_active",
                "blocked_details": None, "concentration": 0.8, "top_positions": positions,
                "trader_tags": {"tas": "Degen", "tcs": "Streaky"},
                "detected_at": f"2026-10-06T{23 - i // 4:02d}:{59 - i:02d}:00Z"}

    pos = [{"market": "BTC", "delta_pnl": 1_000_000, "direction": "long", "leverage": 10}]
    events = [event(0, "0xaaa", "sent", pos), event(1, "0xbbb", "blocked", pos)]
    events += [event(i, ("0xaaa", "0xbbb")[i % 2], "blocked", None) for i in range(2, 48)]
    events += [event(48, "0xaaa", "sent", pos), event(49, "0xccc", "blocked", [])]
    responses = {
        "leaderboard_get_status": {"status": {
            "window": "4h", "base_snapshot_timestamp": 1, "last_update_timestamp": 2,
            "total_leaderboard_traders": 5000, "prices": {f"xyz:ASSET{i}": 1.0 + i for i in range(800)}}},
        "leaderboard_get_markets": {"markets": {"markets": [
            {"token": f"A{i}", "dex": "", "direction": "long", "max_leverage": 20,
             "pct_of_top_traders_gain": 50 - i / 10, "contribution_pct_change_15m": 0.1,
             "contribution_pct_change_1h": 0.2, "contribution_pct_change_4h": 0.3,
             "token_price_change_pct_15m": 0.1, "token_price_change_pct_1h": 0.5,
             "token_price_change_pct_4h": 1.5, "day_notional_volume": 1e8, "trader_count": 40,
             "is_dominant_direction": True} for i in range(200)],
            "source_trader_count": 100, "window": "4h", "timestamp": 3}},
        "leaderboard_get_top": {"leaderboard": {"data": [
            {"rank": i + 1, "trader_id": f"0x{i:040x}", "unrealized_pnl": 1e6 - i, "pnl_percentage": 12.5,
             "ath_delta": -100.0, "top_markets": ["BTC", "ETH", "HYPE"],
             "trader_tags": {"tas": "Active", "tcs": "Elite", "trp_score": 80, "trp_label": "High"}}
            for i in range(200)], "window": "4h", "timestamp": 3, "total_traders": 5000}, "count": 200},
        "leaderboard_get_momentum_events": {"events": {"events": events, "query": {
            "from": "2026-10-06T20:00:00Z", "to": "2026-10-07T00:00:00Z", "limit": 50, "tier": None,
            "asset": None}, "total_count": 50}},
    }

    class Client:
        def mcp_call(self, tool, timeout=12, **kw):
            return {"success": True, "data": responses.get(tool)}
    return Client()


def test_smart_money_projects_and_bounds_live_shape():
    """The raw reads run to ~200k chars and OpenClaw truncates at ~16k: the overlay must come back as
    complete JSON carrying the narrated fields, the source counts, and one event per trader."""
    meta = {"warnings": []}
    smart = pulse.fetch_smart_money(_live_sized_client(), meta)
    assert meta["warnings"] == []
    assert len(json.dumps(smart, ensure_ascii=False)) < 6_000

    assert smart["status"] == {"window": "4h", "last_update_timestamp": 2, "total_leaderboard_traders": 5000}

    markets = smart["concentration"]
    assert len(markets["rows"]) == 8 and markets["total_count"] == 200
    assert markets["source_trader_count"] == 100
    assert markets["rows"][0]["token"] == "A0" and "day_notional_volume" not in markets["rows"][0]

    top = smart["top_traders"]
    assert len(top["rows"]) == 5 and top["total_count"] == 200 and top["total_traders"] == 5000
    assert top["rows"][0] == {"rank": 1, "trader_id": "0x" + "0" * 40, "unrealized_pnl": 1e6,
                              "pnl_percentage": 12.5, "top_markets": ["BTC", "ETH", "HYPE"]}

    events = smart["momentum_events"]
    assert events["total_count"] == 50
    # one event per trader, its newest (or its newest sent); blocked and positionless both kept
    assert [(e["trader_id"], e["decision"], e["delta_pnl"]) for e in events["rows"]] == [
        ("0xaaa", "sent", 2_000_000), ("0xbbb", "blocked", 2_000_001), ("0xccc", "blocked", 2_000_049)]
    assert [bool(e.get("top_positions")) for e in events["rows"]] == [True, True, False]


def _momentum_client(events, total_count=None, calls=None):
    """`_live_sized_client` with the momentum read replaced; `calls` records each tool's arguments."""
    base = _live_sized_client()

    class Client:
        def mcp_call(self, tool, timeout=12, **kw):
            if calls is not None:
                calls[tool] = kw
            if tool == "leaderboard_get_momentum_events":
                return {"success": True, "data": {"events": {"events": events, "query": {}, "total_count":
                        len(events) if total_count is None else total_count}}}
            return base.mcp_call(tool)
    return Client()


def _blocked(trader, i, reason="concentration_too_low"):
    """A momentum event as prod returned every one of 1,807 on 2026-10-09: blocked, no positions,
    no concentration or tags, and free-text blocked_details."""
    return {"trader_id": trader, "tier": 1, "tier_label": "Tier 1 (Exceptional, $2+)", "delta_pnl": 5_016_915.6 - i,
            "decision": "blocked", "blocked_reason": reason, "blocked_details": "Concentration 62.3% < 70%",
            "concentration": None, "top_positions": None, "trader_tags": None,
            "detected_at": f"2026-10-09T20:{42 - i // 12:02d}:{59 - i % 60:02d}.470Z"}


def test_positionless_blocked_events_are_narrated_one_per_trader():
    """Prod today: every event is a blocked, positionless re-fire of the same few traders. The layer
    must still name each trader's newest crossing instead of coming back empty."""
    traders = ("0xb83de012dba672c76a7dbbbf3e459cb59d7d6e36", "0x5b5d51203a0f9079f8aeb098a6523a13f298c060",
               "0x45d26f28c3a7d1e0b9f4e2a6c8d0b1f3e5a7c9d1")
    events = [_blocked(traders[i % 2] if i < 190 else traders[2], i,
                       "trader_cooldown_active" if i % 2 else "concentration_too_low") for i in range(200)]
    calls, meta = {}, {"warnings": []}
    smart = pulse.fetch_smart_money(_momentum_client(events, total_count=1835, calls=calls), meta)
    assert meta["warnings"] == []
    assert calls["leaderboard_get_momentum_events"] == {"limit": 200}     # the widest window the tool gives
    layer = smart["momentum_events"]
    assert layer["total_count"] == 1835
    assert layer["rows"] == [
        {"trader_id": traders[0], "tier_label": "Tier 1 (Exceptional, $2+)", "delta_pnl": 5_016_915.6,
         "decision": "blocked", "blocked_reason": "concentration_too_low", "detected_at": events[0]["detected_at"]},
        {"trader_id": traders[1], "tier_label": "Tier 1 (Exceptional, $2+)", "delta_pnl": 5_016_914.6,
         "decision": "blocked", "blocked_reason": "trader_cooldown_active", "detected_at": events[1]["detected_at"]},
        {"trader_id": traders[2], "tier_label": "Tier 1 (Exceptional, $2+)", "delta_pnl": 5_016_725.6,
         "decision": "blocked", "blocked_reason": "concentration_too_low", "detected_at": events[190]["detected_at"]},
    ]


def test_sent_events_lead_and_win_within_a_trader():
    """A trader's sent event beats its newer blocked repeats, and sent traders list before blocked ones
    (then newest first); the cap still holds."""
    pos = [{"market": m, "delta_pnl": 1e6, "direction": "short", "leverage": 5} for m in ("ETH", "BTC", "SOL", "HYPE")]
    events = [_blocked(f"0xb{i:02d}", i) for i in range(10)]
    events.insert(3, _blocked("0xb05", 3, "trader_cooldown_active"))          # 0xb05 again, newer than its sent one
    events.append(dict(_blocked("0xb05", 11), decision="sent", blocked_reason=None, top_positions=pos))
    smart = pulse.fetch_smart_money(_momentum_client(events), {"warnings": []})
    rows = smart["momentum_events"]["rows"]
    assert len(rows) == pulse.SMART_EVENT_LIMIT
    assert rows[0]["trader_id"] == "0xb05" and rows[0]["decision"] == "sent"
    assert [p["market"] for p in rows[0]["top_positions"]] == ["ETH", "BTC", "SOL"]
    assert [r["trader_id"] for r in rows[1:]] == ["0xb00", "0xb01", "0xb02", "0xb03", "0xb04", "0xb06", "0xb07"]
    assert all("blocked_details" not in r for r in rows)


def test_a_renamed_envelope_is_unavailable_not_empty():
    """Schema drift (the list moved or was renamed) must read as a null layer plus a warning — an empty
    `rows` would narrate as "nobody crossed a tier"."""
    class Client:
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "leaderboard_get_momentum_events":
                return {"success": True, "data": {"momentum": {"events": [_blocked("0xb83", 0)]}}}
            if tool == "leaderboard_get_status":
                return {"success": True, "data": {"window": "4h"}}             # lost its `status` wrapper
            return _live_sized_client().mcp_call(tool)

    meta = {"warnings": []}
    smart = pulse.fetch_smart_money(Client(), meta)
    assert smart["momentum_events"] is None and smart["status"] is None
    assert smart["concentration"]["rows"]                                     # the rest still lands
    assert any(w.startswith("leaderboard_get_momentum_events failed") and "events.events" in w
               for w in meta["warnings"])
    assert any("leaderboard_get_status" in w for w in meta["warnings"])


def test_an_empty_event_list_is_a_real_empty():
    meta = {"warnings": []}
    smart = pulse.fetch_smart_money(_momentum_client([]), meta)
    assert smart["momentum_events"] == {"total_count": 0, "rows": []} and meta["warnings"] == []


def test_a_failed_leaderboard_read_is_unavailable_not_empty():
    """A read the MCP refused must not narrate as "no traders" — it is null, with a warning."""
    class Client:
        def mcp_call(self, tool, timeout=12, **kw):
            if tool == "leaderboard_get_top":
                return {"success": False, "error": "upstream 503"}
            return _live_sized_client().mcp_call(tool)

    meta = {"warnings": []}
    smart = pulse.fetch_smart_money(Client(), meta)
    assert smart["top_traders"] is None and smart["concentration"]["rows"]
    assert any("leaderboard_get_top" in w for w in meta["warnings"])


def test_fails_open_on_empty():
    """No data anywhere → still valid structure, flagged degraded, no exception."""
    res = pulse.run(pulse._FixtureClient({}), want_smart=True)
    assert "groups" in res and "meta" in res
    assert res["meta"].get("degraded")


# ──────────────────────────────────────────────────────────── streaming steps (pulse · smart · all)
def test_step_pulse_emits_only_its_slice():
    """`pulse` = the FAST core market read: groups/signals/day_classification, NO smart_money key."""
    p = pulse.step_pulse(_client(), want_smart=True, state_path=_state_path())
    assert set(p.keys()) == {"as_of", "day_classification", "signals", "groups", "meta"}
    assert "smart_money" not in p                       # the overlay is the separate `smart` step
    assert p["day_classification"]["label"] == "risk_off"
    assert p["groups"]["crypto"]["avg_change_pct"] is not None
    assert p["signals"]["funding_regime"] == "neutral"  # movers were deep-pulled (funding regime folded in)


def test_step_smart_emits_only_its_slice():
    """`smart` = the heavier overlay: prints ONLY {smart_money, meta} (with smart_money_available)."""
    s = pulse.step_smart(_client(), want_smart=True, state_path=_state_path())
    assert set(s.keys()) == {"smart_money", "meta"}
    assert s["meta"]["smart_money_available"] is True
    assert s["smart_money"]["concentration"]["rows"][0]["token"] == "HYPE"


def test_pulse_then_smart_reproduces_all():
    """pulse → smart over the SHARED state file reproduces the composed `all` (core + overlay)."""
    sp = _state_path()
    p = pulse.step_pulse(_client(), want_smart=True, state_path=sp)
    s = pulse.step_smart(_client(), want_smart=True, state_path=sp)   # reads the state pulse wrote
    composed = {
        "as_of": p["as_of"],
        "day_classification": p["day_classification"],
        "signals": p["signals"],
        "groups": p["groups"],
        "smart_money": s["smart_money"],
    }
    ref = pulse.run(_client(), want_smart=True)
    assert composed == {k: ref[k] for k in composed}


def test_all_is_byte_identical_to_run():
    """`all` (via _all_and_persist) prints byte-for-byte what the untouched run() produced."""
    ref = json.dumps(pulse.run(_client(), want_smart=True), ensure_ascii=False)
    got = json.dumps(pulse._all_and_persist(_client(), True, _state_path()), ensure_ascii=False)
    assert got == ref


def test_smart_self_heals_on_absent_state():
    """`smart` standalone (no prior `pulse`, empty state dir) self-heals the core read, still overlays."""
    sp = _state_path()                                  # dir exists, state file does NOT
    s = pulse.step_smart(_client(), want_smart=True, state_path=sp)
    assert s["smart_money"] is not None                 # overlay still landed
    st = json.load(open(sp))                            # and the recomputed core was persisted
    assert "crypto" in (st.get("groups") or {})


def test_step_fails_open_on_corrupt_state():
    """A corrupt/unreadable state file → recompute (never crash); the slice is still valid."""
    sp = _state_path()
    with open(sp, "w") as fh:
        fh.write("{ this is not valid json ]]]")
    p = pulse.step_pulse(_client(), want_smart=True, state_path=sp)   # pulse overwrites the corrupt file
    assert p["groups"]["crypto"]["avg_change_pct"] is not None
    s = pulse.step_smart(_client(), want_smart=True, state_path=sp)
    assert s["smart_money"] is not None


def test_step_no_smart_yields_null_overlay():
    """`smart --no-smart` returns a clean null overlay (available False), no exception."""
    s = pulse.step_smart(_client(), want_smart=False, state_path=_state_path())
    assert s["smart_money"] is None and s["meta"]["smart_money_available"] is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ✓ {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} passed")
