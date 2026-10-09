"""The shared protection rule, pinned by the fixture set both engines must pass.

`tests/fixtures/protection-fixtures.v1.json` is the CANONICAL repo copy of the set senpi's
ExternalWallet.state (moxie) also vendors byte-identically. Never edit it: a change is a new
protection-fixtures.v2.json, authored with the plan and vendored to both consumers (moxie and this
desk) with a new pin. The sha check is what makes a drifted copy fail CI.

Every case runs through `metrics.open_book` — the desk's real parse path, not the rule alone — so the
dex split, the order lists and the derived naked / partial lists are all under test."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import hashlib
import json
import os
import sys
from decimal import Decimal

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import metrics  # noqa: E402
import render  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "protection-fixtures.v1.json")
SHA256 = "876daa03dc8bb93cb3181ebe9e64fb7fa24844be465c6224e7601b5242cc5dd9"


def _raw():
    with open(FIXTURE, "rb") as fh:
        return fh.read()


def _cases():
    return json.loads(_raw())["cases"]


def test_the_fixture_file_is_the_pinned_v1():
    assert hashlib.sha256(_raw()).hexdigest() == SHA256, (
        "protection-fixtures.v1.json drifted from the shared set — never edit a vendored copy; author "
        "protection-fixtures.v2.json and re-vendor it to every consumer")
    doc = json.loads(_raw())
    assert doc["schema"] == "senpi.protection-fixture/v1" and doc["version"] == 1


def _dex(coin):
    return coin.split(":", 1)[0] if ":" in coin else ""


def _ctxs(aps):
    """metaAndAssetCtxs for the coins a case holds, mark = positionValue / |szi| (HL's own mark)."""
    return [{"universe": [{"name": ap["position"]["coin"]} for ap in aps]},
            [{"markPx": str(Decimal(ap["position"]["positionValue"]) / abs(Decimal(ap["position"]["szi"]))),
              "funding": "0"} for ap in aps]]


def _open_book(case):
    inp = case["input"]
    main = [ap for ap in inp["assetPositions"] if _dex(ap["position"]["coin"]) == ""]
    xyz = [ap for ap in inp["assetPositions"] if _dex(ap["position"]["coin"]) == "xyz"]
    cs = {"assetPositions": main, "marginSummary": {"accountValue": "1000", "totalMarginUsed": "0"}, "withdrawable": "0"}
    cs_xyz = {"assetPositions": xyz, "marginSummary": {"accountValue": "0", "totalMarginUsed": "0"}, "withdrawable": "0"}
    return metrics.open_book(cs, inp["frontendOpenOrders"][""], _ctxs(main), None,
                             cs_xyz, inp["frontendOpenOrders"]["xyz"], _ctxs(xyz))


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_open_book_follows_the_shared_rule(case):
    book = _open_book(case)
    got = {(p["dex"], p["coin"]): p for p in book["positions"]}
    want = {(w["dex"], w["coin"]): w for w in case["expect"]["positions"]}
    assert set(got) == set(want), (sorted(got), sorted(want))
    for key, w in want.items():
        g = got[key]
        assert g["side"] == w["side"] and g["protection"] == w["protection"], (key, g["protection"])
        assert Decimal(str(g["size"])) == Decimal(w["size"]), key
        assert Decimal(g["covered_size"]) == Decimal(w["coveredSize"]), (key, g["covered_size"])
        gs = {s["oid"]: s for s in g["stops"]}
        ws = {s["oid"]: s for s in w["stopOrders"]}
        assert set(gs) == set(ws), (key, sorted(gs), sorted(ws))
        for oid, ws_ in ws.items():
            gs_ = gs[oid]
            assert (gs_["kind"], gs_["status"], gs_["isPositionTpsl"]) == \
                (ws_["kind"], ws_["status"], ws_["isPositionTpsl"]), (key, oid)
            assert Decimal(gs_["size"]) == Decimal(ws_["size"]), (key, oid)
    # naked / partial are DERIVED from protection — no coverage threshold anywhere
    assert sorted(book["naked"]) == sorted(w["coin"] for w in want.values() if w["protection"] == "NONE")
    assert sorted(book["partial"]) == sorted(w["coin"] for w in want.values() if w["protection"] == "PARTIAL")


def test_a_cached_run_without_protection_reads_its_share_with_no_threshold():
    """Runs cached before 1.42.0 carry only `stop_covered_share`. 0.95 was "protected" under the
    old 0.9 rule; it is PARTIAL now, on a re-render too."""
    assert metrics.protection_of(dict(stop_covered_share=0.0)) == "NONE"
    assert metrics.protection_of(dict(stop_covered_share=0.95)) == "PARTIAL"
    assert metrics.protection_of(dict(stop_covered_share=1.0)) == "FULL"
    assert metrics.protection_of(dict()) == "NONE"
    assert metrics.protection_of(dict(protection="PARTIAL", stop_covered_share=1.0)) == "PARTIAL"


def _row(**kw):
    base = dict(coin="BTC", side="LONG", leverage=5, liq_distance_pct=40.0, stop_covered_share=0.0,
                stop_distance_pct=None, stops=[], protection="NONE")
    base.update(kw)
    return base


def test_the_audit_note_reads_protection_not_a_threshold():
    assert render._protection_note(_row(stop_covered_share=0.95, protection="PARTIAL"), None)[0] == "PARTLY COVERED"
    assert render._protection_note(_row(stop_covered_share=1.0, protection="FULL", liq_distance_pct=3.0), None)[0] == "PROTECTED"
    status, note = render._protection_note(_row(stops=[dict(oid="1", kind="TRAILING", status="WAITING_TO_ACTIVATE",
                                                            isPositionTpsl=False, size="0.1")]), None)
    assert status == "UNPROTECTED" and "waiting to activate" in note
