#!/usr/bin/env python3
"""Offline engine test — runs portfolio.run() against a recorded MCP fixture (no network).

The fixture reproduces the canonical bug scenario: embedded wallet holds ~$0 idle, all funds are in
strategies, and `total_withdrawable` is large. The test guards that the engine reports those as
SEPARATE buckets and never collapses strategy-margin into "embedded idle."

    python3 -m pytest senpi-portfolio/tests/   # or: python3 tests/test_portfolio.py
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import portfolio  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "portfolio_fixture.json")
# The shape `openclaw senpi runtime list --json` emits — ONE registered runtime (kodiak-main).
RUNTIMES_FIXTURE = os.path.join(HERE, "fixtures", "runtimes_list.json")
# the wallet the runtimes fixture registers kodiak-main under
KODIAK_WALLET = "0xKODIAK00000000000000000000000000000kdk"
# ACTIVE + funded + attributed, and ABSENT from the runtime list — the trap `not_running` exists to catch
GHOST_WALLET = "0xGHOST00000000000000000000000000000ghost"
# the two wallets of the cougar long/short pair (one strategy, two instances)
COUGAR_LONG_WALLET = "0xCOUGARLONG000000000000000000000000000lng"
COUGAR_SHORT_WALLET = "0xCOUGARSHORT00000000000000000000000000sht"

_TMP = tempfile.mkdtemp(prefix="senpi-portfolio-tests-")
# The engine SHELLS OUT (`openclaw senpi runtime list --json`) — so on a host that actually HAS the CLI
# these tests would read that host's real runtimes. Empty PATH while THIS module's tests run: the only
# way data reaches the engine here is the offline hooks ($SENPI_RUNTIMES_FIXTURE / $SENPI_STATUS_FIXTURE),
# and a test that sets neither exercises the REAL spawn-failure path deterministically.
_EMPTY_BIN = os.path.join(_TMP, "empty-bin")
os.makedirs(_EMPTY_BIN, exist_ok=True)
_SAVED_PATH = "unset"  # sentinel distinct from `None` (PATH legitimately absent from the environment)


def _empty_path():
    """Save the real PATH, then empty it. Must bracket ONLY this module's own test run: setting PATH
    at import time with no restore is what starved `test_run_sh.py`'s `subprocess.run(["bash", ...])`
    of `bash` for the rest of the pytest process — collection imports every test module up front, so
    an unrestored mutation here outlives this file."""
    global _SAVED_PATH
    _SAVED_PATH = os.environ.get("PATH", None)
    os.environ["PATH"] = _EMPTY_BIN


def _restore_path():
    global _SAVED_PATH
    if _SAVED_PATH == "unset":
        return  # _empty_path() never ran — nothing to undo
    if _SAVED_PATH is None:
        os.environ.pop("PATH", None)
    else:
        os.environ["PATH"] = _SAVED_PATH
    _SAVED_PATH = "unset"


def setup_module(module):  # pytest xunit-style hook — runs once before this module's tests
    _empty_path()


def teardown_module(module):  # runs once after this module's tests, restoring PATH for every module after
    _restore_path()


def _write_runtimes(payload):
    """Persist a `runtime list --json` payload; return the path to hand to $SENPI_RUNTIMES_FIXTURE."""
    fd, path = tempfile.mkstemp(suffix=".json", dir=_TMP)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    return path


def _runtimes_payload():
    with open(RUNTIMES_FIXTURE) as fh:
        return json.load(fh)


def fixture_with_status(status):
    """The canonical runtimes fixture with kodiak-main's `status` replaced (running / stopped /
    `running — NO ENTRY SCANNERS`)."""
    payload = _runtimes_payload()
    payload["runtimes"][0]["status"] = status
    return _write_runtimes(payload)


def fixture_with_descriptor(patch):
    """The canonical runtimes fixture with kodiak-main's `descriptor` patched (or replaced by null —
    what the producer emits when the entry's YAML was absent/unparseable). Honors the producer's
    guarantee that hasExit/dslPreset/dsl agree: hasExit False ⇒ the other two are null."""
    payload = _runtimes_payload()
    entry = payload["runtimes"][0]
    if patch is None:
        entry["descriptor"] = None
        return _write_runtimes(payload)
    entry["descriptor"].update(patch)
    if entry["descriptor"].get("hasExit") is not True:
        entry["descriptor"]["dslPreset"] = None
        entry["descriptor"]["dsl"] = None
    return _write_runtimes(payload)


def _engine_fixture():
    """The default MCP fixture behind `run_engine`: ONE strategy the runtime list registers (kodiak,
    $200 idle) + ONE that is ACTIVE, funded $2,000 and attributed but has NO runtime behind it (ghost)."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 2200, "total_withdrawable": 2200,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": [
            {"strategyName": "kodiak", "tradingStrategyName": "kodiak",
             "strategyMetadata": {"skillName": "kodiak", "skillVersion": "1.0.0"},
             "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE", "totalFunded": 200},
            {"strategyName": "gibbon", "tradingStrategyName": "gibbon",
             "strategyMetadata": {"skillName": "gibbon", "skillVersion": "1.0.0"},
             "strategyWalletAddress": GHOST_WALLET, "status": "ACTIVE", "totalFunded": 2000}]},
    }
    for w, usd in ((KODIAK_WALLET, "200"), (GHOST_WALLET, "2000")):
        fixture[f"strategy_get_clearinghouse_state::{w.lower()}"] = {
            "main": {"marginSummary": {"accountValue": usd}, "withdrawable": usd, "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": usd}, "withdrawable": usd, "assetPositions": []}}
    return fixture


def run_engine(runtimes_fixture=RUNTIMES_FIXTURE, status_fixture=None, mcp_fixture=None,
               cli_missing=False, want_market=False):
    """Run the engine offline against `mcp_fixture` (default `_engine_fixture`), with the runtime read
    served from `runtimes_fixture`. `cli_missing=True` (or `runtimes_fixture=None`) leaves the hook unset
    so the engine really tries to spawn the CLI — which cannot be found on this module's emptied PATH."""
    wanted = {"SENPI_RUNTIMES_FIXTURE": None if cli_missing else runtimes_fixture,
              "SENPI_STATUS_FIXTURE": status_fixture}
    saved = {k: os.environ.get(k) for k in wanted}
    try:
        for k, v in wanted.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return portfolio.run(portfolio._FixtureClient(mcp_fixture or _engine_fixture()),
                             want_market=want_market)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def by_wallet(out, wallet):
    """The strategy row for `wallet` — rows are keyed by wallet, never by a name a fixture chose."""
    return next(s for s in out["strategies"] if str(s["wallet"]).lower() == str(wallet).lower())


def _result():
    with open(FIXTURE) as f:
        client = portfolio._FixtureClient(json.load(f))
    return portfolio.run(client, want_market=True)


def test_embedded_idle_is_not_total_withdrawable():
    """THE bug guard: embedded idle is the $1.51 EVM USDC, NOT the $2,301 total_withdrawable."""
    t = _result()["totals"]
    assert t["idle_in_embedded"] == 1.51                 # only the EVM USDC; HL embedded is $0
    assert t["idle_in_strategies"] > 2000                # this is where total_withdrawable lives
    assert t["idle_in_embedded"] != t["idle_in_strategies"]


def test_three_buckets_sum_to_total():
    t = _result()["totals"]
    s = t["idle_in_embedded"] + t["idle_in_strategies"] + t["deployed_in_positions"]
    assert abs(s - t["grand_total_usd"]) <= 2.0          # the three buckets reconcile to the total


def test_shared_dex_collateral_not_double_counted():
    """REGRESSION: `withdrawable` is shared/mirrored across the main+xyz views — count it ONCE.
    cub-short raw: main.av 1149.42 / xyz.av 970.67 / shared withdrawable 740.32.
    Correct wallet value = 1149.42 + 970.67 − 740.32 = 1379.77 (NOT 2120.09 summed, NOT 1149.42 max)."""
    strat = {s["name"]: s for s in _result()["strategies"]}
    short = strat["cub-short"]
    assert short["idle_withdrawable"] == 740.32          # shared idle, counted once (not 1480.64)
    assert short["account_value"] == 1379.77             # main.av + xyz.av − shared idle
    assert short["deployed"] == 639.45                   # position equity across BOTH dexes (409.10 + 230.35)
    # and the grand total reflects it — ~$3,103, not the double-counted ~$5,560
    t = _result()["totals"]
    assert 3050 <= t["grand_total_usd"] <= 3150


def test_canonical_fixture_sleeves_group_by_attribution_not_by_name():
    """The canonical fixture is a 3-sleeve cub deploy, so it must exercise the multi-wallet path: three
    distinct instance names, ONE group re-uniting them, keyed off the `strategyMetadata.skillName` stamp
    `deploy.py` writes. (It previously carried a distinct `tradingStrategyName` per row and no stamp —
    a shape the backend cannot produce, since that field IS the stamp — so the three sleeves fell into
    three wallet-keyed groups of one and the grouping this skill leads with was never exercised here.)"""
    res = _result()
    assert [s["name"] for s in res["strategies"]] == ["cub-long", "cub-short", "cub-preipo"]
    assert all(s["name_source"] == "strategyName" for s in res["strategies"])
    assert all(s["skill_name"] == "cub" for s in res["strategies"])
    groups = res["strategy_groups"]
    assert len(groups) == 1 and groups[0]["label"] == "cub"
    assert groups[0]["is_multi_wallet"] is True
    assert len(groups[0]["instances"]) == 3
    # `protected` does NOT follow the stamp. This run has no runtime read behind it (no CLI on PATH),
    # so no exit was ever read — every row reports null, not the reassuring True the stamp used to buy.
    # That True is the defect: an attributed strategy whose runtime was never registered was rendered
    # "✅ DSL protected" on exactly this evidence.
    assert all(s["protected"] is None for s in res["strategies"])
    assert all(s["runtime_health"] == "unverified" for s in res["strategies"])


def test_embedded_address_resolved():
    e = _result()["embedded_wallet"]
    assert e["address"] == "0xembed00000000000000000000000000000000ed"
    assert e["idle_hl_usdc"] == 0                         # all funds moved into strategies


def test_short_book_positions_and_market_alignment():
    """cub-short holds 3 shorts, all working WITH today's selloff."""
    strat = {s["name"]: s for s in _result()["strategies"]}
    short = strat["cub-short"]
    assert len(short["positions"]) == 3
    assert all(p["direction"] == "short" for p in short["positions"])
    eth = next(p for p in short["positions"] if p["asset"] == "ETH")
    assert eth["market_24h_pct"] < 0 and eth["vs_market"] == "with the move"
    assert eth["return_on_equity_pct"] == 11.9           # leveraged return, not raw price %


def test_flat_strategies_are_all_idle():
    strat = {s["name"]: s for s in _result()["strategies"]}
    lng = strat["cub-long"]
    assert lng["positions"] == [] and lng["deployed"] == 0
    assert lng["idle_withdrawable"] > 1500               # 100% free margin


def test_exposure_net_short():
    exp = _result()["exposure"]
    assert exp["net_bias"] == "short"                    # every open position is a short
    assert exp["gross_long_usd"] == 0


def test_fails_open_on_empty():
    res = portfolio.run(portfolio._FixtureClient({}), want_market=True)
    assert "totals" in res and res["meta"].get("degraded")


def test_profile_description_comes_from_the_engine_descriptor():
    """The mandate is the engine's rendered description — the skill no longer parses runtime.yaml."""
    out = run_engine(runtimes_fixture=RUNTIMES_FIXTURE)
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["profile"]["description"] == "SOL alpha hunter"
    # the RUNTIME answered, never the catalog alone (whether catalog facets also landed depends only on
    # whether a catalog.json is reachable from wherever this runs — it never supplies the description)
    assert kodiak["profile"]["source"] in ("registry", "registry+catalog")
    assert kodiak["profile"]["runtime_name"] == "kodiak-main"
    assert kodiak["profile"]["group"] == "kodiak"
    assert kodiak["profile"]["version"] == 7


def test_registered_running_runtime_is_not_not_running():
    out = run_engine(runtimes_fixture=RUNTIMES_FIXTURE)
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["runtime_registered"] is True
    assert kodiak["not_running"] is False
    assert kodiak["running_blind"] is False
    assert kodiak["protected"] is True            # descriptor.hasExit — an exit the ENGINE read


def test_active_funded_strategy_absent_from_the_list_is_not_running():
    """The trap this field exists to catch: ACTIVE + funded + stamped, with no runtime behind it."""
    out = run_engine(runtimes_fixture=RUNTIMES_FIXTURE)
    ghost = by_wallet(out, GHOST_WALLET)
    assert ghost["runtime_registered"] is False
    assert ghost["not_running"] is True
    assert ghost["protected"] is False
    assert ghost["runtime_health"] == "not_running"
    assert out["meta"].get("not_running") == ["gibbon"]
    assert any("not running" in w.lower() for w in out["meta"]["warnings"])


def test_running_blind_is_surfaced():
    """`running — NO ENTRY SCANNERS`: the runtime is up and cannot produce entry signals. Before this,
    this surface rendered it as plain healthy."""
    out = run_engine(runtimes_fixture=fixture_with_status("running — NO ENTRY SCANNERS"))
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["running_blind"] is True
    assert kodiak["runtime_health"] == "degraded"
    assert kodiak["runtime_registered"] is True    # it IS registered and up — just blind
    assert out["meta"].get("running_blind") == ["kodiak"]
    assert any("no entry scanners" in w.lower() for w in out["meta"]["warnings"])


def test_a_listed_runtime_with_no_status_does_not_claim_it_can_enter():
    """A dropped field must buy nothing. If the inventory row carries no `status` — key missing, or
    present and null — we never checked whether that runtime has entry scanners, so `running_blind` is
    null, not False. False there asserts "we checked, and it can enter positions"; and with telemetry
    answering healthy the row would otherwise read running_blind False + runtime_health live."""
    for label, payload in (("missing key", _runtimes_payload()), ("explicit null", _runtimes_payload())):
        entry = payload["runtimes"][0]
        if label == "missing key":
            entry.pop("status")
        else:
            entry["status"] = None
        res = _run_with_status({"kodiak-main": status_doc({"health": "healthy"})},
                               runtimes_fixture=_write_runtimes(payload))
        strat = by_wallet(res, KODIAK_WALLET)
        assert strat["running_blind"] is None, label
        assert strat["runtime_registered"] is True, label      # it IS listed — that much we did check
        assert strat["runtime_health"] == "unknown", label     # and telemetry cannot upgrade it to live
        assert res["strategy_groups"][0]["running_blind"] is None, label
        assert res["strategy_groups"][0]["runtime_health"] == "unknown", label


def test_a_stopped_runtime_never_reads_live():
    """`stopped` is the producer's third status. It is registered, so it is not `not_running` — but a
    stopped process is not working either, and must never fall through to a 'live' verdict."""
    out = run_engine(runtimes_fixture=fixture_with_status("stopped"))
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["runtime_registered"] is True
    assert kodiak["runtime_health"] == "degraded"


def test_protected_is_false_when_the_descriptor_reports_no_exit():
    """`protected` must come from an exit actually read, never from the presence of a skillName.
    (`hasExit` is the ENGINE's funding-gate predicate — stricter than the `exit:`-block-exists test this
    skill used to apply, so some strategies correctly flip from protected to unprotected here.)"""
    out = run_engine(runtimes_fixture=fixture_with_descriptor({"hasExit": False, "dsl": None}))
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["skill_name"]          # attribution IS present
    assert kodiak["protected"] is False  # and it is not what decides this


def test_unreadable_cli_degrades_to_null_and_warns_loudly():
    """The whole finding in one test: no field may degrade to its reassuring value, and the
    sourcing failure may not be silent."""
    out = run_engine(runtimes_fixture=None, cli_missing=True)
    for s in out["strategies"]:
        assert s["runtime_registered"] is None
        assert s["not_running"] is None
        assert s["running_blind"] is None
        assert s["protected"] is None
        assert s["runtime_health"] == "unverified"
    assert out["meta"]["warnings"], "a silent sourcing failure is the defect"
    assert any("runtime list" in w for w in out["meta"]["warnings"])
    # and the group roll-up may not launder those nulls into a verdict either
    for g in out["strategy_groups"]:
        assert g["protected"] is None and g["not_running"] is None
        assert g["runtime_health"] == "unverified"


def _raise_oserror(*_a, **_kw):
    """What `subprocess.run` really does on a memory-constrained box: the FORK fails, so the child
    never runs. Not a FileNotFoundError — an ENOMEM OSError, which used to propagate."""
    raise OSError(12, "Cannot allocate memory")


def test_a_fork_failure_is_a_failed_read_not_a_dead_engine():
    """A spawn that fails for any reason other than a missing binary must degrade like any other
    failed read. It used to propagate out of the registry read, through fetch_strategies, out of
    run() — taking the money map, the positions and the whole JSON answer with it, on the exact
    kind of strained box where a user most wants to know where their money is."""
    real = portfolio.subprocess.run
    portfolio.subprocess.run = _raise_oserror
    try:
        rc, out, err = portfolio._run_cli(["openclaw", "senpi", "runtime", "list", "--json"])
        assert (rc, out) == (-1, "")
        # never-ran, so it carries the never-ran prefix the money path in deploy.py branches on —
        # a timeout (ran, still in flight) remains the only rc=-1 without it
        assert err.startswith(portfolio.SPAWN_FAILED_PREFIX)
        assert "Cannot allocate memory" in err
        res = run_engine(cli_missing=True)
    finally:
        portfolio.subprocess.run = real
    assert len(res["strategies"]) == 2, "the whole read died with the spawn"
    assert res["totals"]["grand_total_usd"] > 0
    for s in res["strategies"]:
        assert s["runtime_health"] == "unverified"
        assert s["protected"] is None
    assert any("runtime list" in w for w in res["meta"]["warnings"])


def test_a_payload_that_is_not_ok_is_a_failed_read_not_an_empty_fleet():
    """`ok: false` means the runtime could not answer. Reading that as 'zero runtimes' would flag every
    funded strategy not_running — a false alarm as bad as the false all-clear."""
    out = run_engine(runtimes_fixture=_write_runtimes({"ok": False, "error": "gateway unreachable"}))
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["runtime_registered"] is None
    assert kodiak["runtime_health"] == "unverified"
    assert any("runtime list" in w for w in out["meta"]["warnings"])


def test_an_empty_runtimes_list_is_a_successful_read_of_zero_runtimes():
    """The other side of it: `{ok: true, runtimes: []}` IS an answer — the box has no runtimes, so an
    ACTIVE + funded + attributed strategy really is not running."""
    out = run_engine(runtimes_fixture=_write_runtimes({"ok": True, "runtimes": []}))
    kodiak = by_wallet(out, KODIAK_WALLET)
    assert kodiak["runtime_registered"] is False
    assert kodiak["not_running"] is True
    assert kodiak["protected"] is False


def _kodiak_only_fixture():
    """One ACTIVE kodiak strategy on the wallet the runtimes fixture registers (id=kodiak-main)."""
    return {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 200, "total_withdrawable": 200,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "kodiak", "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{KODIAK_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "200"}, "withdrawable": "200", "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "200"}, "withdrawable": "200", "assetPositions": []}},
    }


def status_doc(*records):
    """The DOCUMENT `openclaw senpi status -r <id> --json` really prints: `{ok: true, statuses: [...]}`.

    Every telemetry fixture here goes through this, because a fixture that invents a shape the producer
    never emits certifies nothing: these tests were green over a mapper that found no verdict in ANY
    real payload and defaulted to 'live'. The shape is the producer's own — senpi-trading-runtime
    `src/cli/senpi-commands.ts` writes `writeJson({ ok: true, statuses })`, and each record is a
    `RuntimeHealthStatus` (`health`, `components`, …) — and it is what `_cli.runtime_health_map` reads."""
    return {"ok": True, "statuses": list(records)}


def _run_with_status(status_by_id, runtimes_fixture=RUNTIMES_FIXTURE, mcp_fixture=None):
    """Run the engine with kodiak-main registered (the runtimes fixture) and a `senpi status` telemetry
    fixture keyed by runtime id — no subprocess. Each value is the whole document that id's call prints
    (build it with `status_doc`). Returns the result dict."""
    return run_engine(runtimes_fixture=runtimes_fixture,
                      status_fixture=_write_runtimes(status_by_id),
                      mcp_fixture=mcp_fixture or _kodiak_only_fixture())

def test_registered_runtime_healthy_status_is_live():
    """A registered runtime whose `senpi status` telemetry reports healthy → runtime_health 'live'."""
    res = _run_with_status({"kodiak-main": status_doc({"health": "healthy", "activePositions": 0})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_registered"] is True
    assert strat["runtime_health"] == "live"
    assert "degraded_runtimes" not in res["meta"]


def test_registered_runtime_degraded_status_is_flagged():
    """Registered runtime whose telemetry reports degraded/unhealthy → runtime_health 'degraded' + warning
    (running, but not cleanly — distinct from not_running and from live)."""
    res = _run_with_status({"kodiak-main": status_doc({"health": "degraded"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_health"] == "degraded"
    assert res["meta"].get("degraded_runtimes") == ["kodiak"]
    assert any("degraded" in w.lower() for w in res["meta"]["warnings"])


def test_the_runtime_the_engine_calls_unhealthy_never_reads_live():
    """THE regression this branch made load-bearing, measured against the document the producer really
    writes: `{ok, statuses:[{health:'unhealthy', …}]}`. The verdict lives INSIDE `statuses[]`; a mapper
    that searched only the wrapper found none, fell through to its 'live' default, and reported live +
    protected + no warning for a runtime the engine itself called unhealthy."""
    res = _run_with_status({"kodiak-main": status_doc(
        {"runtimeName": "kodiak-main", "health": "unhealthy", "lastError": "scan threw"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_health"] == "degraded"
    assert res["meta"].get("degraded_runtimes") == ["kodiak"]
    assert any("degraded" in w.lower() for w in res["meta"]["warnings"]), "and it may not be silent"


def test_no_running_runtime_at_all_is_not_live():
    """`{ok: true, statuses: []}` — the gateway answered, and it is running NO runtime under that id.
    That is the emptiest possible answer and it must not read as confirmation: 'unknown' (unproven), and
    not 'degraded' either — registered-vs-not is the registry read's verdict, not telemetry's."""
    res = _run_with_status({"kodiak-main": status_doc()})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_registered"] is True     # the registry DID list it
    assert strat["runtime_health"] == "unknown"
    assert "degraded_runtimes" not in res["meta"]


def test_a_run_state_never_promotes_a_runtime_to_live():
    """`status: "running"` is a RUN STATE, not a health verdict: it proves a process exists, never that
    it works. Promoting one is the incident `_cli.py`'s HEALTH_KEYS/_RUN_STATE_KEYS split records — a
    `{name, status: 'running'}` row rendered as a ✅ for a runtime no tick had ever proven."""
    res = _run_with_status({"kodiak-main": status_doc({"runtimeName": "kodiak-main",
                                                       "status": "running"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_health"] == "unknown"
    assert "degraded_runtimes" not in res["meta"]


def test_registered_runtime_no_telemetry_is_unknown():
    """Registered runtime but telemetry has no entry for it (and no subprocess) → runtime_health 'unknown'
    — liveness unverified, never asserted broken."""
    res = _run_with_status({"some-other-runtime-id": status_doc({"health": "healthy"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_registered"] is True
    assert strat["runtime_health"] == "unknown"


def test_runtime_reported_unknown_is_not_live():
    """The runtime's own overall health of `unknown` (never-heard scanner, just-restarted runtime) must NOT
    be painted 'live' — it is UNPROVEN, not confirmed working. It also must not join the DEGRADED warning
    list: unproven is not broken."""
    res = _run_with_status({"kodiak-main": status_doc({"health": "unknown"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_registered"] is True
    assert strat["runtime_health"] == "unknown"
    assert "degraded_runtimes" not in res["meta"]


def test_unrecognised_health_verdict_is_not_live():
    """Fail-closed on vocabulary drift: a verdict in neither the healthy nor the broken family (here the
    runtime's `disabled`) reads 'unknown', never 'live'."""
    res = _run_with_status({"kodiak-main": status_doc({"health": "disabled"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_health"] == "unknown"
    assert "degraded_runtimes" not in res["meta"]


def test_liveness_mapping_table():
    """Pin the whole `_liveness_from_status` mapping in one place, against the REAL document shape:
    healthy→live, degraded/unhealthy→degraded, unknown/disabled→unknown, empty `statuses[]`→unknown,
    a run state→unknown (never promoted) unless it is a broken one (→degraded), and a document with no
    verdict we recognise→unknown. Nothing but a health verdict earns 'live'."""
    doc = status_doc
    assert portfolio._liveness_from_status(doc({"health": "healthy"})) == "live"
    assert portfolio._liveness_from_status(doc({"health": "ok"})) == "live"
    assert portfolio._liveness_from_status(doc({"health": "degraded"})) == "degraded"
    assert portfolio._liveness_from_status(doc({"health": "unhealthy"})) == "degraded"
    assert portfolio._liveness_from_status(doc({"health": "unknown"})) == "unknown"
    assert portfolio._liveness_from_status(doc({"health": "disabled"})) == "unknown"
    assert portfolio._liveness_from_status(doc({"health": "sparkling"})) == "unknown"
    assert portfolio._liveness_from_status(doc()) == "unknown"              # answered: no runtime running
    assert portfolio._liveness_from_status(doc({"status": "running"})) == "unknown"   # run state ≠ health
    assert portfolio._liveness_from_status(doc({"status": "stopped"})) == "degraded"  # …may downgrade
    assert portfolio._liveness_from_status(doc({"activePositions": 2})) == "unknown"  # no verdict at all
    # worst wins across records — one sick runtime is not averaged away by a healthy sibling
    assert portfolio._liveness_from_status(
        doc({"health": "healthy"}, {"health": "unhealthy"})) == "degraded"
    # the single-record shape the `-r <id>` form may hand back, and a bare record, still classify
    assert portfolio._liveness_from_status({"ok": True, "status": {"health": "healthy"}}) == "live"
    assert portfolio._liveness_from_status({"overallHealth": "healthy"}) == "live"
    assert portfolio._liveness_from_status({"data": {"health": "unknown"}}) == "unknown"
    assert portfolio._liveness_from_status({"ok": False, "error": "gateway unreachable"}) == "unknown"
    assert portfolio._liveness_from_status({}) == "unknown"
    assert portfolio._liveness_from_status(None) == "unknown"
    assert portfolio._liveness_from_status([{"health": "healthy"}]) == "unknown"


def test_nested_component_status_does_not_falsely_degrade():
    """A runtime whose OVERALL verdict is healthy but whose nested per-scanner health is unhealthy must
    read 'live', not 'degraded'. Deep-matching a health key anywhere would cry DEGRADED on a runtime the
    engine itself calls healthy — a false alarm. The overall verdict is `RuntimeHealthStatus.health`."""
    res = _run_with_status({"kodiak-main": status_doc(
        {"runtimeName": "kodiak-main", "health": "healthy", "activePositions": 2,
         "components": {"scanners": {"component": "scanners", "health": "unhealthy",
                                     "scanners": [{"scannerId": "kodiak_signals",
                                                   "health": "unhealthy"}]}}})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_health"] == "live"
    assert "degraded_runtimes" not in res["meta"]


def test_a_stopped_run_state_still_classifies_degraded():
    """The other side of the promotion rule: a run state may only DOWNGRADE, and it still does — a
    record reporting `status: "stopped"` reads degraded, not unknown."""
    res = _run_with_status({"kodiak-main": status_doc({"status": "stopped"})})
    strat = {s["name"]: s for s in res["strategies"]}["kodiak"]
    assert strat["runtime_health"] == "degraded"


def test_registered_runtime_with_a_null_descriptor_is_still_running():
    """The producer emits `descriptor: null` when an entry's YAML was absent or unparseable. That runtime
    is still REGISTERED and still RUNNING — never flagged not_running. But its exit was never read, so it
    is not asserted protected either: undescribed is undescribed in BOTH directions."""
    res = _run_with_status({"kodiak-main": status_doc({"health": "healthy"})},
                           runtimes_fixture=fixture_with_descriptor(None))
    strat = by_wallet(res, KODIAK_WALLET)
    assert strat["runtime_registered"] is True      # present in the list → registered
    assert strat["not_running"] is False            # registered ⇒ NOT "not running"
    assert strat["runtime_health"] == "live"        # telemetry says healthy
    assert strat["protected"] is False              # no exit was read — nothing earns True here
    assert strat["profile"] is None                 # undescribed …
    assert "not_running" not in res["meta"]         # … which must NOT downgrade it to not-running


def _cougar_runtimes():
    """A `runtime list --json` payload for the cougar pair — two instances of ONE strategy (shared
    `group`), which is what re-unites them into a single strategy_groups[] entry."""
    def entry(name, wallet, sleeve):
        return {"id": name, "wallet": wallet, "source": "(inline)", "status": "running",
                "descriptor": {"name": name, "group": "cougar", "version": 1,
                               "description": f"COUGAR — a market-neutral long/short pair. This {sleeve} "
                                              f"sleeve holds its half of the book.",
                               "hasExit": True, "dslPreset": "conviction",
                               "dsl": {"preset_name": "conviction",
                                       "note": "named preset — ladder not inlined"}}}
    return _write_runtimes({"ok": True, "runtimes": [
        entry("cougar-long", COUGAR_LONG_WALLET, "LONG"),
        entry("cougar-short", COUGAR_SHORT_WALLET, "SHORT")]})


def test_multi_wallet_strategy_groups_into_one():
    """A STRATEGY IS ALL ITS WALLETS. cougar deploys as TWO instances on TWO wallets (cougar-long +
    cougar-short, sharing `group: cougar` in the descriptors the runtime renders). `strategy_list` returns
    them as two separate rows; the engine must re-unite them into ONE `strategy_groups[]` entry with
    `is_multi_wallet: true` and 2 instances — never present the two sleeves as two strategies."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 2000, "total_withdrawable": 1200,
            "total_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": [
            {"strategyName": "cougar-long", "tradingStrategyName": "cougar",
             "strategyMetadata": {"skillName": "cougar", "skillVersion": "1.0.0"},
             "strategyWalletAddress": COUGAR_LONG_WALLET, "status": "ACTIVE"},
            {"strategyName": "cougar-short", "tradingStrategyName": "cougar",
             "strategyMetadata": {"skillName": "cougar", "skillVersion": "1.0.0"},
             "strategyWalletAddress": COUGAR_SHORT_WALLET, "status": "ACTIVE"}]},
        # long sleeve: flat, all idle (its other-sleeve-waiting-for-signal case)
        f"strategy_get_clearinghouse_state::{COUGAR_LONG_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000", "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000", "assetPositions": []}},
        # short sleeve: one working short
        f"strategy_get_clearinghouse_state::{COUGAR_SHORT_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "800", "assetPositions": [
                {"position": {"coin": "ETH", "szi": -0.5, "positionValue": 800, "marginUsed": 200,
                              "entryPx": 1719.7, "unrealizedPnl": 20, "returnOnEquity": 0.1,
                              "leverage": {"value": 4}, "liquidationPx": 2100}}]},
            "xyz": {"marginSummary": {"accountValue": "800"}, "withdrawable": "800", "assetPositions": []}},
    }
    res = run_engine(runtimes_fixture=_cougar_runtimes(), mcp_fixture=fixture)

    # still two per-wallet rows in strategies[] (bucket math relies on it) …
    assert len(res["strategies"]) == 2
    # … but ONE strategy_groups[] entry re-uniting them
    groups = res["strategy_groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["label"] == "cougar"
    assert g["is_multi_wallet"] is True
    assert len(g["instances"]) == 2
    names = {i["name"] for i in g["instances"]}
    assert names == {"cougar-long", "cougar-short"}
    # the flat long sleeve is its OTHER book waiting for a signal — surfaced, not "dead money"
    assert g["flat_instances"] == ["cougar-long"]
    # totals summed across BOTH wallets
    assert g["totals"]["account_value"] == 1000 + 1000        # long wallet + short wallet
    assert g["totals"]["deployed"] == 200                     # only the short sleeve has a position
    assert g["totals"]["upnl"] == 20
    # mandate shared across instances (from the descriptor the runtime rendered)
    assert isinstance(g["mandate"], str) and "market-neutral" in g["mandate"]
    # meta flag flips on
    assert res["meta"]["has_multi_wallet_strategy"] is True


def test_single_wallet_strategy_is_one_instance_group():
    """A single-instance strategy is its own group with is_multi_wallet: false and one instance —
    and with no multi-wallet strategy present, the meta flag stays False."""
    res = run_engine(mcp_fixture=_kodiak_only_fixture())
    groups = res["strategy_groups"]
    assert len(groups) == 1
    g = groups[0]
    assert g["label"] == "kodiak"                # descriptor.group, via profile.group
    assert g["is_multi_wallet"] is False
    assert len(g["instances"]) == 1
    assert res["meta"]["has_multi_wallet_strategy"] is False


def test_dsl_ladder_comes_from_the_descriptor():
    """(1) HOW DSL works: the engine renders the ladder (phase1 hard-stop floor + the phase2 tiers) into
    `descriptor.dsl`, and this skill reports it verbatim — the CONFIG side of the "protected from entry"
    story. Nothing here re-derives it from a runtime.yaml the skill never reads."""
    res = run_engine(mcp_fixture=_kodiak_only_fixture())
    dsl = by_wallet(res, KODIAK_WALLET)["profile"]["dsl"]
    assert dsl is not None
    assert dsl["hard_stop_roe_pct"] == -14          # the hard floor, active FROM ENTRY
    assert dsl["arm_at_roe_pct"] == 8               # where the profit-ratchet ARMS
    assert dsl["has_phase2"] is True
    assert dsl["tiers"] == [{"trigger_pct": 8, "lock_hw_pct": 50}]
    # the group surfaces the ladder once per strategy too
    assert res["strategy_groups"][0]["dsl"]["arm_at_roe_pct"] == 8


def test_named_preset_dsl_is_reported_not_dropped():
    """A NAMED string preset ("conviction") renders as a preset name + a note, never None — the ladder
    just isn't inlined. Reported as-is; a named preset is never "no DSL"."""
    res = run_engine(runtimes_fixture=_cougar_runtimes(), mcp_fixture={
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 100, "total_withdrawable": 100,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": [
            {"strategyName": "cougar-long", "strategyWalletAddress": COUGAR_LONG_WALLET,
             "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{COUGAR_LONG_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100", "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100", "assetPositions": []}},
    })
    prof = by_wallet(res, COUGAR_LONG_WALLET)["profile"]
    assert prof["dsl"] == {"preset_name": "conviction", "note": "named preset — ladder not inlined"}
    assert prof["dsl_preset"] == "conviction"


def test_live_position_dsl_armed_and_unarmed():
    """(2) WHICH open position is in WHICH tier — the core fix.

    Two open positions on the kodiak strategy:
      - SOL: a LIVE ratchet record at tier 0 → dsl.armed True, tier_index 0, locked = lock_hw_pct at
        tier 0 (from the descriptor's ladder = 50).
      - ETH: NO ratchet record (sub-Tier-1) → dsl.armed False, but framed as PROTECTED from entry with
        the arm-at note — NEVER a falsy/'none' that reads as unprotected.
    ratchet_stop_list is keyed by wallet in the fixture (the engine calls it with strategy_wallet_address)."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 500, "total_withdrawable": 100,
            "total_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "kodiak", "id": "strat-kodiak-1",
             "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{KODIAK_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "500"}, "withdrawable": "100", "assetPositions": [
                # SOL: deep in profit, has crossed Tier 1 → will get a live ratchet record
                {"position": {"coin": "SOL", "szi": 3.0, "positionValue": 600, "marginUsed": 120,
                              "entryPx": 150, "unrealizedPnl": 40, "returnOnEquity": 0.33,
                              "leverage": {"value": 5}, "liquidationPx": 90}},
                # ETH: only +6% ROE, sub-Tier-1 → NO ratchet record, must still read as protected
                {"position": {"coin": "ETH", "szi": 0.2, "positionValue": 400, "marginUsed": 100,
                              "entryPx": 1700, "unrealizedPnl": 6, "returnOnEquity": 0.06,
                              "leverage": {"value": 4}, "liquidationPx": 1300}}]},
            "xyz": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100", "assetPositions": []}},
        # LIVE ratchet state — only SOL has crossed Tier 1. ETH is absent BY DESIGN.
        f"ratchet_stop_list::{KODIAK_WALLET.lower()}": {"configs": [
            {"asset": "SOL", "status": "ACTIVE", "currentTierIndex": 0, "highWaterRoe": 36.5}]},
    }
    res = run_engine(mcp_fixture=fixture)
    pos = {p["asset"]: p for p in by_wallet(res, KODIAK_WALLET)["positions"]}

    # SOL — armed at the live tier, with the locked % pulled from the descriptor's ladder
    sol = pos["SOL"]["dsl"]
    assert sol["armed"] is True
    assert sol["tier_index"] == 0
    assert sol["high_water_roe"] == 36.5
    assert sol["status"] == "ACTIVE"
    assert sol["locked"] == 50          # lock_hw_pct at tier 0

    # ETH — NO ratchet record, but NEVER unprotected: armed False + the arm-at framing, and the note
    # must read as PROTECTED, not as a gap.
    eth = pos["ETH"]["dsl"]
    assert eth["armed"] is False
    assert eth["hard_stop_roe_pct"] == -14      # phase1 floor still protecting from entry
    assert eth["arm_at_roe_pct"] == 8           # ratchet arms at Tier 1 (+8%)
    assert eth["roe"] == 6.0                    # this position is at +6% — below the arm point
    assert "protected from entry" in eth["note"]
    low = eth["note"].lower()
    assert "no dsl" not in low and "unprotected" not in low and "no monitoring" not in low


def test_live_position_dsl_fails_open_when_ratchet_call_absent():
    """If the ratchet_stop_list call returns nothing at all (no fixture entry → the engine's list read
    yields no records), an open position STILL gets a config-based dsl object (armed False + arm-at
    framing) that stands alone — never left looking unprotected."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 500, "total_withdrawable": 100,
            "total_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "kodiak", "id": "strat-kodiak-1",
             "strategyWalletAddress": KODIAK_WALLET, "status": "ACTIVE"}]},
        f"strategy_get_clearinghouse_state::{KODIAK_WALLET.lower()}": {
            "main": {"marginSummary": {"accountValue": "500"}, "withdrawable": "100", "assetPositions": [
                {"position": {"coin": "SOL", "szi": 3.0, "positionValue": 600, "marginUsed": 120,
                              "entryPx": 150, "unrealizedPnl": 40, "returnOnEquity": 0.33,
                              "leverage": {"value": 5}, "liquidationPx": 90}}]},
            "xyz": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100", "assetPositions": []}},
        # NOTE: no ratchet_stop_list::<wallet> fixture — the list call yields no records at all.
    }
    res = run_engine(mcp_fixture=fixture)
    sol = by_wallet(res, KODIAK_WALLET)["positions"][0]["dsl"]
    assert sol["armed"] is False
    assert sol["hard_stop_roe_pct"] == -14
    assert sol["arm_at_roe_pct"] == 8
    assert "protected from entry" in sol["note"]

def test_embedded_idle_reads_nested_total_in_hyperliquid():
    """Regression (the invisible-$10k bug): account_get_portfolio nests balances under a `portfolio`
    key and the idle-HL field is `total_in_hyperliquid` — NOT `total_usdc_in_hyperliquid`. The old code
    missed both, so embedded idle always read $0 and a large infusion was invisible. This fixture
    (nested + correct field, $10,446 idle, no strategies) must surface as idle-in-embedded; it reads $0
    under the pre-fix code."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 10446.0, "total_allocated_in_strategy": 0, "total_withdrawable": 0,
            "total_in_hyperliquid": 10446.0, "total_spot_usd_in_hyperliquid": 0, "token_balances": []}},
        "strategy_list": {"strategies": []},
    }
    res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    assert res["embedded_wallet"]["idle_hl_usdc"] == 10446.0
    assert res["totals"]["idle_in_embedded"] == 10446.0


def test_embedded_evm_usdc_reads_balanceInUSD():
    """Regression (the always-empty evm_usdc bug): live GetPortfolioV3 token_balances carry the USD
    amount as `balanceInUSD` (with `tokenSymbol`/`formattedBalance`), none of which were in the old
    field-name fallback chain — so every EVM token read $0 and evm_usdc was always []. This fixture
    (the exact live shape, USDC on Base) must surface as embedded EVM idle."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 7.98, "total_allocated_in_strategy": 0, "total_withdrawable": 0,
            "total_in_hyperliquid": 0, "total_spot_usd_in_hyperliquid": 0,
            "token_balances": [{
                "tokenSymbol": "USDC",
                "tokenAddress": "0x833589fcd6edb6e8f4c7c32d4f71b54bda02913",
                "formattedBalance": "7.984101",
                "balanceInUSD": 7.983940679251919,
                "chainId": 8453}]}},
        "strategy_list": {"strategies": []},
    }
    res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    assert res["embedded_wallet"]["evm_usdc"] == [{"chain": "EVM", "usd": 7.98}]
    assert res["totals"]["idle_in_embedded"] == 7.98


def test_embedded_evm_usdc_falls_back_to_formattedBalance_times_price():
    """When `balanceInUSD` is absent the amount comes from formattedBalance x tokenPriceInUSD."""
    fixture = {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"portfolio": {
            "total_balance_usd": 5.0, "total_allocated_in_strategy": 0, "total_withdrawable": 0,
            "total_in_hyperliquid": 0, "total_spot_usd_in_hyperliquid": 0,
            "token_balances": [{
                "tokenSymbol": "USDC",
                "formattedBalance": "5.0",
                "tokenPriceInUSD": 0.9998,
                "chainId": 42161}]}},
        "strategy_list": {"strategies": []},
    }
    res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    assert res["embedded_wallet"]["evm_usdc"] == [{"chain": "EVM", "usd": 5.0}]
    assert res["totals"]["idle_in_embedded"] == 5.0


# ──────────────────────────────────────────────── streaming STEPS (money · strategies · positions · all)
def _client():
    """A fresh fixture client on the canonical portfolio fixture (each step consumes its own client)."""
    with open(FIXTURE) as f:
        return portfolio._FixtureClient(json.load(f))


def _tmp_state():
    return os.path.join(tempfile.mkdtemp(), "state.json")


def test_step_money_emits_the_three_buckets_offline():
    """STEP `money` — the fast money map: the three buckets + grand_total + reconciles, offline against
    the fixture. Same bucket values run() produces (idle-in-embedded is the $1.51 EVM USDC, NOT the
    $2,461 idle-in-strategies), computed WITHOUT the positions/DSL/closed detail."""
    out = portfolio.step_money(_client(), want_market=True, state_path=_tmp_state())
    t = out["totals"]
    assert set(("grand_total_usd", "idle_in_embedded", "idle_in_strategies",
                "deployed_in_positions", "reconciles")) <= set(t)
    assert t["idle_in_embedded"] == 1.51                  # only the EVM USDC (bucket 1)
    assert t["idle_in_strategies"] > 2000                 # total_withdrawable lives here (bucket 2)
    assert t["deployed_in_positions"] == 639.45           # margin backing open positions (bucket 3)
    assert 3050 <= t["grand_total_usd"] <= 3150
    s = t["idle_in_embedded"] + t["idle_in_strategies"] + t["deployed_in_positions"]
    assert abs(s - t["grand_total_usd"]) <= 2.0           # the buckets reconcile to the total
    # money-lite strategy rows carry the money fields but NOT the heavy detail
    assert len(out["strategies"]) == 3
    row = out["strategies"][0]
    assert "account_value" in row and "idle_withdrawable" in row and "deployed" in row
    assert "positions" not in row and "profile" not in row and "closed" not in row


def test_step_strategies_emits_per_strategy_detail_offline():
    """STEP `strategies` — the per-strategy verdict surface: fully-hydrated strategies[] (positions +
    mandate/DSL + closed) + strategy_groups[]. Self-heals its own fetch when state is absent."""
    out = portfolio.step_strategies(_client(), want_market=False, state_path=_tmp_state())
    assert len(out["strategies"]) == 3
    assert len(out["strategy_groups"]) >= 1
    short = {s["name"]: s for s in out["strategies"]}["cub-short"]
    assert len(short["positions"]) == 3                   # full positions detail present
    assert "closed" in short                              # closed/realized block present
    # each open position carries its live DSL tier object (never left looking unprotected)
    assert all("dsl" in p for p in short["positions"])


def test_step_positions_emits_market_exposure_signals_offline():
    """STEP `positions` — the position-level slice: market enrichment folded onto positions
    (market_24h_pct/vs_market) + exposure + signals. The market fan-out is isolated in this step."""
    out = portfolio.step_positions(_client(), want_market=True, state_path=_tmp_state())
    assert out["exposure"]["net_bias"] == "short"         # every open position is a short
    assert out["exposure"]["gross_long_usd"] == 0
    assert set(("idle_drag_pct", "deployed_pct")) <= set(out["signals"])
    short = {s["name"]: s for s in out["strategies"]}["cub-short"]
    eth = next(p for p in short["positions"] if p["asset"] == "ETH")
    assert eth["market_24h_pct"] < 0 and eth["vs_market"] == "with the move"


def test_every_step_reports_where_the_runtime_read_came_from():
    """`meta.registry_source` is documented as always present, and it is what a reader checks before
    trusting any runtime claim on a row. The `positions` step used to drop it on the state-HIT path (the
    read ran in an earlier step), so its output carried runtime-derived fields with no provenance beside
    them. Pinned on both steps that carry those fields, and pinned to AGREE. (`money` is exempt by
    design: it makes no runtime read and its rows carry no runtime-derived field to qualify.)"""
    saved = os.environ.get("SENPI_RUNTIMES_FIXTURE")
    os.environ["SENPI_RUNTIMES_FIXTURE"] = RUNTIMES_FIXTURE
    try:
        sp = _tmp_state()
        portfolio.step_money(_client(), want_market=False, state_path=sp)
        outs = [portfolio.step_strategies(_client(), want_market=False, state_path=sp),
                portfolio.step_positions(_client(), want_market=False, state_path=sp)]
    finally:
        if saved is None:
            os.environ.pop("SENPI_RUNTIMES_FIXTURE", None)
        else:
            os.environ["SENPI_RUNTIMES_FIXTURE"] = saved
    for key in ("registry_source", "catalog_source", "profile_source", "runtime_read_ok"):
        assert all(key in o["meta"] for o in outs), key
        assert len({json.dumps(o["meta"][key]) for o in outs}) == 1, key
    assert outs[-1]["meta"]["registry_source"] == "runtime-cli"
    assert outs[-1]["meta"]["runtime_read_ok"] is True


def test_step_sequence_reproduces_all_values():
    """money → strategies → positions over ONE shared state reproduces `all`'s values: the money buckets,
    and (after the market-folding positions step) the enriched strategies[], strategy_groups, exposure,
    signals, and full totals all match run()/`all` exactly."""
    allres = portfolio._all_and_persist(_client(), want_market=True, state_path=_tmp_state())
    sp = _tmp_state()
    m = portfolio.step_money(_client(), want_market=True, state_path=sp)
    s = portfolio.step_strategies(_client(), want_market=True, state_path=sp)
    p = portfolio.step_positions(_client(), want_market=True, state_path=sp)
    # money buckets match all
    for k in ("grand_total_usd", "idle_in_embedded", "idle_in_strategies", "deployed_in_positions",
              "reconciles"):
        assert m["totals"][k] == allres["totals"][k]
    # after the positions step (which folds market, then rebuilds groups over the enriched positions,
    # exactly as run() does) the full picture matches all exactly
    assert p["strategies"] == allres["strategies"]
    assert p["strategy_groups"] == allres["strategy_groups"]
    assert p["exposure"] == allres["exposure"]
    assert p["signals"] == allres["signals"]
    assert p["totals"] == allres["totals"]
    # the `strategies` step also produced groups (pre-market) — a valid standalone verdict surface
    assert len(s["strategy_groups"]) == len(allres["strategy_groups"])


def test_all_step_is_byte_identical_to_run():
    """`all` (via _all_and_persist) is BYTE-IDENTICAL to run() — the steps machinery must not perturb the
    one-shot composed output. State is written to a temp path so no real state file is touched."""
    direct = portfolio.run(_client(), want_market=True)
    allres = portfolio._all_and_persist(_client(), want_market=True, state_path=_tmp_state())
    a = json.dumps(direct, ensure_ascii=False, sort_keys=True)
    b = json.dumps(allres, ensure_ascii=False, sort_keys=True)
    assert a == b


def test_steps_self_heal_on_absent_state():
    """Each step works STANDALONE against an ABSENT state file (self-heals its prerequisite fetch). The
    strategies + positions steps recompute the full pull when the state file doesn't exist yet."""
    missing = os.path.join(tempfile.mkdtemp(), "does-not-exist.json")
    assert not os.path.isfile(missing)
    s = portfolio.step_strategies(_client(), want_market=False, state_path=missing)
    assert len(s["strategies"]) == 3                      # recomputed from scratch
    missing2 = os.path.join(tempfile.mkdtemp(), "nope.json")
    p = portfolio.step_positions(_client(), want_market=True, state_path=missing2)
    assert p["exposure"]["net_bias"] == "short"           # self-healed the fetch, then computed exposure


def test_steps_fail_open_on_corrupt_state():
    """A corrupt/garbage state file → each step RECOMPUTES (never crashes). Guards the fail-open contract:
    the money map, the per-strategy detail, and the positions analysis all recover from unparseable state."""
    corrupt = os.path.join(tempfile.mkdtemp(), "state.json")
    with open(corrupt, "w") as f:
        f.write("}{ not json at all ][")
    m = portfolio.step_money(_client(), want_market=True, state_path=corrupt)
    assert 3050 <= m["totals"]["grand_total_usd"] <= 3150   # recovered the money map
    # overwrite corrupt again (money just wrote valid state) and prove strategies/positions also recover
    with open(corrupt, "w") as f:
        f.write("<<<garbage>>>")
    s = portfolio.step_strategies(_client(), want_market=False, state_path=corrupt)
    assert len(s["strategies"]) == 3
    with open(corrupt, "w") as f:
        f.write("null and void")
    p = portfolio.step_positions(_client(), want_market=True, state_path=corrupt)
    assert p["exposure"]["net_bias"] == "short"


def test_closed_but_active_strategy_flagged_empty_not_idle():
    """RECONCILE status vs live wallet: strategy_list can report a just-CLOSED strategy as ACTIVE (status
    lags the close). The engine must flag such a $0 wallet `empty: true` (never count its `total_funded`
    as live/idle capital) — the exact failure where a closed strategy was narrated as holding $3K idle.
    A flat-but-FUNDED strategy (idle margin, no positions) must NOT be flagged empty."""
    WOLF = "0xwolf000000000000000000000000000000000wf"      # CLOSED — drained, reported ACTIVE
    HORNET = "0xhornet00000000000000000000000000000hnt"     # funded + one position
    IDLE = "0xidle000000000000000000000000000000000id"      # funded, flat, waiting (NOT empty)
    fixture = {
        "user_get_me": {"wallets": [{"walletType": "embedded",
                                     "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 12605, "total_withdrawable": 4620,
                                  "total_usdc_in_hyperliquid": 12605, "token_balances": []},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "wolf", "strategyWalletAddress": WOLF, "status": "ACTIVE",
             "id": "wolf-1", "totalFunded": 3000, "totalWithdrawn": 3000},
            {"tradingStrategyName": "hornet", "strategyWalletAddress": HORNET, "status": "ACTIVE",
             "id": "hornet-1", "totalFunded": 4000, "totalWithdrawn": 0},
            {"tradingStrategyName": "idlecat", "strategyWalletAddress": IDLE, "status": "ACTIVE",
             "id": "idle-1", "totalFunded": 2000, "totalWithdrawn": 0},
        ]},
        f"strategy_get_clearinghouse_state::{WOLF.lower()}": {   # EMPTY: closed/drained
            "main": {"marginSummary": {"accountValue": "0"}, "withdrawable": "0", "assetPositions": []},
            "xyz":  {"marginSummary": {"accountValue": "0"}, "withdrawable": "0", "assetPositions": []}},
        f"strategy_get_clearinghouse_state::{HORNET.lower()}": {  # $2000 idle + one position
            "main": {"marginSummary": {"accountValue": "2000"}, "withdrawable": "2000", "assetPositions": []},
            "xyz":  {"marginSummary": {"accountValue": "3420"}, "withdrawable": "2000", "assetPositions": [
                {"position": {"coin": "SKHX", "szi": "3.5", "positionValue": "5679", "marginUsed": "1420",
                              "entryPx": "1594.3", "unrealizedPnl": "42.79", "returnOnEquity": "0.03"}}]}},
        f"strategy_get_clearinghouse_state::{IDLE.lower()}": {    # funded, FLAT (idle margin, no positions)
            "main": {"marginSummary": {"accountValue": "2000"}, "withdrawable": "2000", "assetPositions": []},
            "xyz":  {"marginSummary": {"accountValue": "2000"}, "withdrawable": "2000", "assetPositions": []}},
    }
    res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    strat = {s["name"]: s for s in res["strategies"]}
    # wolf: reported ACTIVE but $0 wallet → empty, closed_or_drained (totalWithdrawn ≈ totalFunded)
    assert strat["wolf"]["empty"] is True
    assert strat["wolf"]["empty_reason"] == "closed_or_drained"
    assert strat["wolf"]["account_value"] == 0 and strat["wolf"]["idle_withdrawable"] == 0
    # a funded-but-flat strategy is NOT empty (idle margin still there)
    assert strat["idlecat"]["empty"] is False
    assert strat["hornet"]["empty"] is False
    # meta surfaces the status/clearinghouse mismatch
    assert "wolf" in res["meta"].get("dormant_active", [])
    # the closed strategy contributes $0 — idle_in_strategies is hornet+idlecat only, NOT +$3K wolf
    assert res["totals"]["idle_in_strategies"] == 4000    # 2000 (hornet) + 2000 (idlecat); no phantom 3K


def test_unfunded_empty_strategy_reason():
    """An ACTIVE strategy never funded ($0 wallet, totalFunded 0) → empty with reason 'unfunded' (distinct
    from closed/drained), still excluded from idle."""
    W = "0xunfund000000000000000000000000000000un"
    fixture = {
        "user_get_me": {"wallets": [{"walletType": "embedded",
                                     "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 100, "total_withdrawable": 0,
                                  "total_usdc_in_hyperliquid": 100, "token_balances": []},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": "newbie", "strategyWalletAddress": W, "status": "ACTIVE",
             "id": "n-1", "totalFunded": 0, "totalWithdrawn": 0}]},
        f"strategy_get_clearinghouse_state::{W.lower()}": {
            "main": {"marginSummary": {"accountValue": "0"}, "withdrawable": "0", "assetPositions": []},
            "xyz":  {"marginSummary": {"accountValue": "0"}, "withdrawable": "0", "assetPositions": []}},
    }
    res = portfolio.run(portfolio._FixtureClient(fixture), want_market=False)
    s = {x["name"]: x for x in res["strategies"]}["newbie"]
    assert s["empty"] is True and s["empty_reason"] == "unfunded"
    assert res["totals"]["idle_in_strategies"] == 0


# ──────────────────────────────────────────────── the strategy's DISPLAY NAME (strategyName, #190)
def test_display_name_prefers_the_strategys_own_name():
    """`strategyName` is the strategy's OWN name; `tradingStrategyName` is the PACKAGE id it was created
    under (`strategy_list` sets it to `strategyMetadata.skillName` verbatim). Prefer the name over the
    package id — strategy-ops already answers this question that way, and the two skills naming the same
    object differently is the divergence."""
    assert portfolio._strategy_name_and_source(
        {"strategyName": "warpath", "tradingStrategyName": "spider"}) == ("warpath", "strategyName")


def test_a_present_but_empty_display_name_falls_through_to_the_package_id():
    """`strategyName` is NULLABLE by mechanism, not by accident: `strategy_create` takes no name at all
    and it is optional on `strategy_create_custom_strategy`. The key is now present on EVERY row, so
    silence at that leg must not answer for the legs behind it. (`""`/`"  "` are unreachable through the
    MCP's create schema — `.trim().min(3).regex(/^\\S+$/)` — this pins the READER, which must not depend
    on that schema holding.)"""
    for empty in (None, "", "   "):
        assert portfolio._strategy_name_and_source(
            {"strategyName": empty, "tradingStrategyName": "spider"}) == ("spider",
                                                                         "tradingStrategyName"), repr(empty)


def test_display_name_falls_back_to_the_placeholder_only_when_every_leg_is_silent():
    assert portfolio._strategy_name_and_source({}) == ("strategy", None)
    assert portfolio._strategy_name_and_source(
        {"strategyName": None, "tradingStrategyName": None, "name": ""}) == ("strategy", None)


def test_a_container_or_flag_in_a_name_field_is_not_a_name():
    """Boundary reader: `_field` hands back whatever the payload held, so a shape drift renders a dict
    as the strategy's name. A container is never a name; a scalar is stringified."""
    assert portfolio._strategy_name_and_source(
        {"strategyName": {"oops": 1}, "tradingStrategyName": "cub"}) == ("cub", "tradingStrategyName")
    assert portfolio._strategy_name_and_source(
        {"strategyName": True, "tradingStrategyName": "cub"}) == ("cub", "tradingStrategyName")
    assert portfolio._strategy_name_and_source({"strategyName": 42}) == ("42", "strategyName")


def test_name_source_separates_a_real_name_from_a_package_id_standing_in():
    """`name` alone cannot tell "this strategy is called cub" from "this strategy is unnamed and cub is
    its package" — and the fallback is the COMMON path (null on 21 of 23 rows in a live sample). The row
    must carry which field answered, or the surface is claiming a name it cannot prove."""
    res = portfolio.run(portfolio._FixtureClient(_multi_instance_fixture()), want_market=False)
    src = {s["name"]: s["name_source"] for s in res["strategies"]}
    assert src["cub-long"] == "strategyName"          # its own name
    assert src["gibbon"] == "tradingStrategyName"     # unnamed — this is the PACKAGE id standing in
    # and the collision the fallback creates is separable: same rendered name, different provenance
    named, unnamed = (portfolio._strategy_name_and_source({"strategyName": "cub"}),
                      portfolio._strategy_name_and_source({"tradingStrategyName": "cub"}))
    assert named[0] == unnamed[0] == "cub" and named[1] != unnamed[1]


def _multi_instance_fixture():
    """Three instances of ONE package as the backend really returns them post-#190: a distinct
    `strategyName` per instance (`deploy.py` creates them as `<id>-<instance>`), the SAME
    `tradingStrategyName` on all three because that field is the package id, and the
    `strategyMetadata.skillName` stamp `deploy.py` writes and `tradingStrategyName` is derived FROM —
    plus a fourth, unnamed strategy from a different package whose `strategyName` is null."""
    wallets = {"cub-long": "0xlong0000000000000000000000000000000long",
               "cub-short": "0xshort000000000000000000000000000000shrt",
               "cub-preipo": "0xpreipo00000000000000000000000000000prei",
               "unnamed": "0xnoname00000000000000000000000000000none"}
    rows = [{"strategyName": n, "tradingStrategyName": "cub",
             "strategyMetadata": {"skillName": "cub", "skillVersion": "1.0.0"},
             "strategyWalletAddress": w, "status": "ACTIVE", "id": f"s-{n}",
             "totalFunded": 100, "totalWithdrawn": 0}
            for n, w in wallets.items() if n != "unnamed"]
    rows.append({"strategyName": None, "tradingStrategyName": "gibbon",
                 "strategyMetadata": {"skillName": "gibbon", "skillVersion": "1.0.0"},
                 "strategyWalletAddress": wallets["unnamed"], "status": "ACTIVE",
                 "id": "s-unnamed", "totalFunded": 100, "totalWithdrawn": 0})
    fixture = {
        "user_get_me": {"wallets": [{"walletType": "embedded",
                                     "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 400, "total_withdrawable": 400,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": rows},
    }
    for w in wallets.values():
        fixture[f"strategy_get_clearinghouse_state::{w.lower()}"] = {
            "main": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100",
                     "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "100"}, "withdrawable": "100",
                    "assetPositions": []}}
    return fixture


def test_instances_of_one_package_are_named_apart_not_collapsed():
    """The user-visible consequence: every instance of a multi-wallet strategy carries the SAME package
    id, so naming rows by `tradingStrategyName` renders three sleeves as three rows all called "cub".
    The instance's own name is what tells the long book from the short book — while the ATTRIBUTION, not
    the name, is what still re-unites them into one strategy."""
    res = portfolio.run(portfolio._FixtureClient(_multi_instance_fixture()), want_market=False)
    names = [s["name"] for s in res["strategies"]]
    assert names == ["cub-long", "cub-short", "cub-preipo", "gibbon"]
    # grouping keys off profile.group → skill_name → wallet, NEVER off the name: the three renamed
    # sleeves still land in ONE cub group, and the unnamed strategy in its own.
    groups = {g["label"]: g for g in res["strategy_groups"]}
    assert set(groups) == {"cub", "gibbon"}
    assert {i["name"] for i in groups["cub"]["instances"]} == {"cub-long", "cub-short", "cub-preipo"}


def test_the_money_map_names_strategies_the_same_way():
    """`fetch_strategy_money` re-derives the row skeleton for the fast money step; ONE reader, so the
    money map and the per-strategy detail can never name the same wallet two different things — nor
    disagree about whether that name was proven."""
    out = portfolio.step_money(portfolio._FixtureClient(_multi_instance_fixture()),
                               want_market=False, state_path=_tmp_state())
    assert [s["name"] for s in out["strategies"]] == ["cub-long", "cub-short", "cub-preipo", "gibbon"]
    assert [s["name_source"] for s in out["strategies"]] == (
        ["strategyName"] * 3 + ["tradingStrategyName"])

def _one_live_fixture(wallet, name="cub"):
    """Fixture: embedded wallet + ONE ACTIVE strategy at `wallet` (empty positions, $1000)."""
    return {
        "user_get_me": {"wallets": [
            {"walletType": "embedded", "walletAddress": "0xembed00000000000000000000000000000000ed"}]},
        "account_get_portfolio": {"total_balance_usd": 1000, "total_withdrawable": 1000,
                                  "total_usdc_in_hyperliquid": 0, "token_balances": []},
        "strategy_list": {"strategies": [
            {"tradingStrategyName": name, "skillName": name, "status": "ACTIVE",
             "id": f"{name}-1", "totalFunded": 1000, "strategyWalletAddress": wallet}]},
        f"strategy_get_clearinghouse_state::{wallet.lower()}": {
            "main": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000", "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "1000"}, "withdrawable": "1000", "assetPositions": []}},
    }


def _ghost_state(ghost_wallet, acct=1950.0):
    """A prior run's persisted state whose strategies_full carries a strategy that has since CLOSED."""
    return {
        "embedded_wallet": {"address": "0xembed00000000000000000000000000000000ed", "idle_total": 0.0},
        "portfolio_totals": {"total_balance_usd": 1000, "total_withdrawable": 1000},
        "strategies_full": [
            {"name": "orangutan", "wallet": ghost_wallet, "strategy_id": "orang-1", "status": "ACTIVE",
             "account_value": acct, "idle_withdrawable": acct - 80, "deployed": 80.0,
             "positions": [{"coin": "SOL", "margin": 80.0}], "empty": False}],
    }


def test_stale_cross_run_state_is_dropped_not_served_as_ghost():
    """The shared state file persists across runs (tempdir). A `strategies_full` cached in a PRIOR run —
    older than STATE_TTL_S — must be discarded, not served: `_load_state` drops it and `strategies`
    self-heals with a fresh fetch. Regression for 'recommend closing an already-closed strategy' (a closed
    strategy served from a stale snapshot as a live ghost)."""
    LIVE = "0xLIVE0000000000000000000000000000000live"
    GHOST = "0xORANG000000000000000000000000000000rng"
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(_ghost_state(GHOST), fh)
        old_mtime = time.time() - (portfolio.STATE_TTL_S + 10)   # a cross-run file, past the TTL
        os.utime(state_path, (old_mtime, old_mtime))
        res = portfolio.step_strategies(portfolio._FixtureClient(_one_live_fixture(LIVE)),
                                        want_market=False, state_path=state_path)
    names = {s["name"] for s in res["strategies"]}
    assert "orangutan" not in names, "closed strategy served from stale cross-run cache (ghost)"
    assert "cub" in names, "live strategy missing after the freshness re-fetch"


def test_step_money_starts_clean_and_wipes_prior_strategies_full():
    """step_money (step 1) starts each turn from a CLEAN state, so a `strategies_full` cached in a prior
    run cannot survive into this turn's `strategies` step — even within the TTL window. This is what makes
    `money` and `strategies` agree by construction (the 9-vs-10 mismatch that started the bug)."""
    LIVE = "0xLIVE0000000000000000000000000000000live"
    GHOST = "0xORANG000000000000000000000000000000rng"
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(_ghost_state(GHOST), fh)          # fresh mtime → within TTL; only step_money's reset drops it
        portfolio.step_money(portfolio._FixtureClient(_one_live_fixture(LIVE)),
                             want_market=False, state_path=state_path)
        with open(state_path) as fh:
            after = json.load(fh)
    assert "strategies_full" not in after, "step_money preserved a prior run's strategies_full into the turn"


def test_within_ttl_cached_state_is_reused():
    """A fresh (within-TTL) state file IS reused — the intra-turn fast path is preserved. The cache carries
    account_value=9999, impossible from the fixture's $1000 clearinghouse; seeing 9999 proves reuse (no
    re-fetch)."""
    W = "0xSAME0000000000000000000000000000000same"
    cached_state = {
        "embedded_wallet": {"address": "0xembed00000000000000000000000000000000ed", "idle_total": 0.0},
        "portfolio_totals": {"total_balance_usd": 1000, "total_withdrawable": 1000},
        "strategies_full": [
            {"name": "cub", "wallet": W, "strategy_id": "cub-1", "status": "ACTIVE",
             "account_value": 9999.0, "idle_withdrawable": 0.0, "deployed": 9999.0, "positions": []}],
    }
    with tempfile.TemporaryDirectory() as td:
        state_path = os.path.join(td, "state.json")
        with open(state_path, "w") as fh:
            json.dump(cached_state, fh)                 # fresh mtime → within TTL → reused
        res = portfolio.step_strategies(portfolio._FixtureClient(_one_live_fixture(W)),
                                        want_market=False, state_path=state_path)
    s = {x["name"]: x for x in res["strategies"]}["cub"]
    assert s["account_value"] == 9999.0, "within-TTL cache was re-fetched (fast path lost)"


# ─────────────────────────────── fee-aware closed PnL (gross vs net, builder-inclusive) ───────────────
FEE_WALLET = "0xFEE0000000000000000000000000000000feefe"
NOFILLS_WALLET = "0xNOFILL000000000000000000000000000nofill"
EMPTY_CLOSED_WALLET = "0xEMPTYCLOSED0000000000000000000000empty"


def _closed_fixture(wallet, closed_positions, fills=None):
    """A `_FixtureClient` serving `discovery_get_trader_history` (keyed by trader_address) and, when
    provided, the HL `userFills` ledger (keyed `hl::userFills::<wallet>` — the same key `_hl_info` reads).
    Omit `fills` entirely to model an UNAVAILABLE fills read (the fixture returns None)."""
    rec = {f"discovery_get_trader_history::{wallet.lower()}": {"closedPositions": closed_positions}}
    if fills is not None:
        rec[f"hl::userFills::{wallet.lower()}"] = fills
    return portfolio._FixtureClient(rec)


def test_closed_net_realized_is_gross_minus_fees_from_the_fills_ledger():
    """A wallet WITH fills: `net_realized_pnl == gross_realized_pnl - fees`, `fees_status == "ok"`, and the
    fee is the builder-INCLUSIVE `fee` summed straight off `userFills` (never gross reported as booked)."""
    closed = [
        {"coin": "ETH", "szi": 1, "realizedPnl": "100", "closeTime": 2000, "entryPx": "1", "exitPx": "2"},
        {"coin": "BTC", "szi": -1, "realizedPnl": "50", "closeTime": 3000, "entryPx": "2", "exitPx": "1"},
    ]
    fills = [
        {"coin": "ETH", "time": 1000, "fee": "1.25", "closedPnl": "0"},     # ETH open leg
        {"coin": "ETH", "time": 2000, "fee": "1.25", "closedPnl": "100"},   # ETH close leg
        {"coin": "BTC", "time": 1500, "fee": "1.00", "closedPnl": "0"},     # BTC open leg
        {"coin": "BTC", "time": 3000, "fee": "1.50", "closedPnl": "50"},    # BTC close leg
        {"coin": "ETH", "time": 9000, "fee": "99.0", "closedPnl": "0"},     # AFTER last close — excluded
    ]
    meta = {}
    out = portfolio.fetch_closed(_closed_fixture(FEE_WALLET, closed, fills), FEE_WALLET, meta)
    assert out["gross_realized_pnl"] == 150.0             # discovery's realizedPnl is GROSS (pre-fee)
    assert out["fees"] == 5.0                             # 1.25+1.25+1.00+1.50 — the 99.0 fill is out-of-window
    assert out["net_realized_pnl"] == 145.0
    assert out["net_realized_pnl"] == round(out["gross_realized_pnl"] - out["fees"], 2)
    assert out["fees_status"] == "ok"
    assert out["trade_count"] == 2
    assert "realized_pnl" not in out                      # the gross-as-booked key is GONE (renamed)


def test_closed_fees_undetermined_when_fills_empty_never_a_fake_zero():
    """DEGRADATION: closed trades EXIST but the `userFills` read comes back an EMPTY list → fees/net are
    null and `fees_status` is "undetermined" (NOT $0 — reporting $0 fees would overstate the user's booked
    profit, the exact bug). A loud warning is emitted; gross is still known. `_hl_info` is forced to return
    a genuine `[]` here (the fixture's `or`-fallback would otherwise collapse `[]` to the None path)."""
    closed = [{"coin": "ETH", "szi": 1, "realizedPnl": "80", "closeTime": 3000, "entryPx": "1", "exitPx": "2"}]
    client = _closed_fixture(NOFILLS_WALLET, closed, fills=None)
    meta = {}
    saved = portfolio._hl_info
    portfolio._hl_info = lambda payload, m, c=None, timeout=12: []      # empty-list read reaching _window_fees
    try:
        out = portfolio.fetch_closed(client, NOFILLS_WALLET, meta)
    finally:
        portfolio._hl_info = saved
    assert out["gross_realized_pnl"] == 80.0
    assert out["fees"] is None                            # NOT 0.0
    assert out["net_realized_pnl"] is None                # NOT 80.0
    assert out["fees_status"] == "undetermined"
    assert out["trade_count"] == 1
    assert any("undetermined" in w for w in meta.get("warnings", [])), "a silent $0, not a loud warning"


def test_closed_fees_undetermined_when_fills_read_unavailable():
    """DEGRADATION (read failure, not empty list): the `userFills` source is UNAVAILABLE (fixture returns
    None) while the wallet has closed trades → same honest degradation, never $0."""
    closed = [{"coin": "BTC", "szi": -1, "realizedPnl": "40", "closeTime": 5000, "entryPx": "2", "exitPx": "1"}]
    meta = {}
    out = portfolio.fetch_closed(_closed_fixture(NOFILLS_WALLET, closed, fills=None), NOFILLS_WALLET, meta)
    assert out["gross_realized_pnl"] == 40.0
    assert out["fees"] is None and out["net_realized_pnl"] is None
    assert out["fees_status"] == "undetermined"


def test_closed_no_trades_is_genuinely_zero_fees_not_undetermined():
    """The ONLY legitimate $0-fees branch: a SUCCESSFUL read of an empty book (zero closed trades). Fees
    are really $0, net == gross == 0.0, `fees_status == "ok"` — and the fills ledger is never even read."""
    meta = {}
    out = portfolio.fetch_closed(_closed_fixture(EMPTY_CLOSED_WALLET, []), EMPTY_CLOSED_WALLET, meta)
    assert out["trade_count"] == 0
    assert out["gross_realized_pnl"] == 0.0
    assert out["fees"] == 0.0                             # genuine zero — distinct from undetermined
    assert out["net_realized_pnl"] == 0.0
    assert out["fees_status"] == "ok"


def _closed_rec(gross, fees, status):
    net = round(gross - fees, 2) if (gross is not None and fees is not None) else None
    return {"gross_realized_pnl": gross, "fees": fees, "net_realized_pnl": net,
            "fees_status": status, "trade_count": 1, "recent": []}


def test_group_totals_sum_net_and_fees_when_every_sleeve_is_ok():
    """A multi-wallet strategy whose every sleeve read fees cleanly: group `totals` sum gross + fees and
    net == gross − fees, `fees_status == "ok"`."""
    rows = [
        {"name": "tigris-long", "wallet": "0xL", "profile": {"group": "tigris"},
         "closed": _closed_rec(100.0, 5.0, "ok")},
        {"name": "tigris-short", "wallet": "0xS", "profile": {"group": "tigris"},
         "closed": _closed_rec(40.0, 3.0, "ok")},
    ]
    groups = portfolio.group_strategies(rows, {})
    t = groups[0]["totals"]
    assert t["gross_realized_pnl"] == 140.0
    assert t["fees"] == 8.0
    assert t["net_realized_pnl"] == 132.0
    assert t["fees_status"] == "ok"
    assert "realized_pnl" not in t                        # renamed at the totals level too


def test_group_totals_are_undetermined_if_any_sleeve_is_undetermined():
    """Honesty roll-up: one sleeve's fees are undetermined → the STRATEGY's fees/net are undetermined too
    (a partial fee sum is never laundered into a complete cost). Gross still sums from what was readable."""
    rows = [
        {"name": "tigris-long", "wallet": "0xL", "profile": {"group": "tigris"},
         "closed": _closed_rec(100.0, 5.0, "ok")},
        {"name": "tigris-short", "wallet": "0xS", "profile": {"group": "tigris"},
         "closed": _closed_rec(40.0, None, "undetermined")},
    ]
    groups = portfolio.group_strategies(rows, {})
    t = groups[0]["totals"]
    assert t["gross_realized_pnl"] == 140.0               # gross is fee-independent — still summed
    assert t["fees"] is None                              # NOT 5.0 (a partial sum)
    assert t["net_realized_pnl"] is None
    assert t["fees_status"] == "undetermined"


if __name__ == "__main__":
    # Direct-script mode bypasses pytest's setup_module/teardown_module hooks entirely, so this run
    # must bracket the same guarantee by hand.
    _empty_path()
    try:
        fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
        for fn in fns:
            fn()
            print(f"  ✓ {fn.__name__}")
        print(f"\n{len(fns)}/{len(fns)} passed")
    finally:
        _restore_path()
