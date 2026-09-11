"""WhaleHunter cohort build is CHUNKED across ticks (no network; fake ctx).
Run: python3 -m pytest strategies/whalehunter/tests -q"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "long", "scanners"))
import scan  # noqa: E402


class _State:
    def __init__(self):
        self.records = []

    def last(self):
        return self.records[-1] if self.records else None

    def append(self, rec):
        self.records.append(rec)


class _Ctx:
    """6 pages of 1,000 traders: realized PnL descends 6,000,000 → 1 so smart (>= $1M) fills from the
    first pages and crowd ($10k–$100k) from the deep pages. Records every MCP call."""
    def __init__(self, fail_on_call=None):
        self.calls = []
        self.state = _State()
        self.wallet = "0xwallet"
        self.fail_on_call = fail_on_call
        self.senpi_mcp = self

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("simulated transport failure")
        if name == "discovery_get_top_traders":
            off = int(args["offset"])
            rows = []
            for i in range(off, off + int(args["limit"])):
                v = 6_000_000 - i * 1000
                rows.append({"address": "0x%040x" % (i + 1), "realizedPnl": v, "realized_pnl": v,
                             "realizedProfitAndLoss": v, "profit_and_loss_realized": v})
            return {"data": rows}
        if name == "discovery_get_trader_state":
            return {"data": {"traders": [{"traderAddress": a, "openPositions": []} for a in args["trader_addresses"]]}}
        return {}


_INPUTS = {"cohortMaxPages": 6, "cohortPagesPerTick": 2, "cohortSampleCap": 5000, "cohortFetchLimit": 1000,
           "cohortRefreshHours": 24, "smartMinRealizedUsd": 1_000_000, "crowdMinRealizedUsd": 10_000,
           "crowdMaxRealizedUsd": 100_000}


def _pages(ctx):
    return sum(1 for n, _ in ctx.calls if n == "discovery_get_top_traders")


def test_a_six_page_refresh_completes_over_three_ticks_serving_nothing_stale():
    ctx = _Ctx()
    c1 = scan._build_cohorts(ctx, {}, _INPUTS, now=1000)
    assert c1.get("build", {}).get("next_page") == 2 and not c1.get("smart") and _pages(ctx) == 2
    c2 = scan._build_cohorts(ctx, c1, _INPUTS, now=1300)
    assert c2["build"]["next_page"] == 4 and _pages(ctx) == 4
    c3 = scan._build_cohorts(ctx, c2, _INPUTS, now=1600)
    assert "build" not in c3 and c3["refreshed_at"] == 1600 and _pages(ctx) == 6
    assert len(c3["smart"]) == 5001 and len(c3["crowd"]) == 91   # 6,000,000..1,000,000 step 1000 ; 100,000..10,000
    # fresh now: no further reads
    c4 = scan._build_cohorts(ctx, c3, _INPUTS, now=1900)
    assert c4 is c3 and _pages(ctx) == 6


def test_a_killed_tick_loses_only_its_own_pages_and_never_double_counts():
    ctx = _Ctx(fail_on_call=3)                      # the 3rd page read raises (tick 2, first page)
    c1 = scan._build_cohorts(ctx, {}, _INPUTS, now=1000)
    c2 = scan._build_cohorts(ctx, c1, _INPUTS, now=1300)  # read fails → progress kept at page 2
    assert c2["build"]["next_page"] == 2
    ctx.fail_on_call = None
    c3 = scan._build_cohorts(ctx, c2, _INPUTS, now=1600)
    c4 = scan._build_cohorts(ctx, c3, _INPUTS, now=1900)
    assert "build" not in c4 and len(set(c4["smart"])) == len(c4["smart"]) == 5001


def test_the_last_complete_cohort_is_served_while_a_new_build_runs():
    old = {"refreshed_at": 0, "cache_version": scan.CACHE_VERSION, "smart": ["0xold"] * 6, "crowd": ["0xc"]}
    ctx = _Ctx()
    c1 = scan._build_cohorts(ctx, old, _INPUTS, now=48 * 3600)   # stale → refresh starts, chunked
    assert c1["smart"] == old["smart"] and c1["build"]["next_page"] == 2


def test_scan_persists_the_build_and_resumes_it_next_tick():
    ctx = _Ctx()
    inputs = dict(_INPUTS, direction="LONG", cohortMinMembers=5)
    out1 = scan.scan(inputs, ctx)
    assert out1 == [] and ctx.state.last()["cohorts"]["build"]["next_page"] == 2
    scan.scan(inputs, ctx)
    scan.scan(inputs, ctx)
    assert "build" not in ctx.state.last()["cohorts"] and len(ctx.state.last()["cohorts"]["smart"]) == 5001
