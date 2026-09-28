"""koala's held-membership and dedup joins must stay symmetric with the emitted symbol.

`asset` is emitted and sent to the venue, so it keeps the operator's casing —
upper() would break `xyz:` prefixes and kPEPE/kBONK. But `held_set` is built
upper (`{h.upper() for h in held}`) and `was_recently_signaled` reads
`signaled[coin.upper()]`, so the *joins* must use the upper form too.

Comparing a raw `asset` against those upper-keyed structures fails for exactly the
names case-preservation exists to support, and the failure is armed by the fix
working: while the venue rejects `KPEPE` nothing fills, `held` stays empty and
nothing stacks. Once the symbol is correct the order fills and the miss compounds —
`record_exit()` fires on a live position, the HOLDING gate falls through, and the
scanner emits a second entry into a name it already holds.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "main", "scanners"))

import scan  # noqa: E402
import scoring  # noqa: E402


class _State:
    def __init__(self, last=None):
        self._l = [last] if last else []

    def __len__(self):
        return len(self._l)

    def last(self):
        return self._l[-1] if self._l else None

    def append(self, d):
        self._l.append(d)


class _MCP:
    def __init__(self, held_coin):
        self.held_coin = held_coin

    def call_tool(self, tool, args):
        return {"data": {"main": {
            "marginSummary": {"accountValue": "1000.0"},
            "assetPositions": [{"position": {
                "coin": self.held_coin, "szi": "1.0", "entryPx": "1.0",
                "positionValue": "100.0", "leverage": {"type": "cross", "value": 5},
            }}],
        }}}


class _Ctx:
    def __init__(self, held_coin, last=None):
        self.wallet = "0x" + "a" * 40
        self.senpi_mcp = _MCP(held_coin)
        self.state = _State(last)
        self.scanner_name = "koala_signals"
        self.interval_seconds = 300


def _run(coin):
    """scan() with `coin` both configured and already held, mid-lifecycle."""
    ctx = _Ctx(coin, last={"koala": {"first_entry_at": 1.0, "total_entries": 1,
                                     "last_exit_at": None}, "signaled": {}})
    return scan.scan({"asset": coin}, ctx), ctx


def _gate(ctx):
    return ((ctx.state.last() or {}).get("result") or {}).get("gate")


def test_holding_gate_fires_for_a_k_prefixed_coin():
    out, ctx = _run("kPEPE")
    assert out == [], "must not emit into a name it already holds"
    assert _gate(ctx) == "holding"


def test_holding_gate_fires_for_a_hip3_coin():
    out, ctx = _run("xyz:GOLD")
    assert out == []
    assert _gate(ctx) == "holding"


def test_record_exit_does_not_fire_while_the_position_is_open():
    _, ctx = _run("kPEPE")
    assert ((ctx.state.last() or {}).get("koala") or {}).get("last_exit_at") is None


def test_plain_uppercase_coin_still_holds():
    out, ctx = _run("BTC")
    assert out == []
    assert _gate(ctx) == "holding"


def test_emitted_symbol_keeps_its_case():
    """Not held -> emits, and the emitted asset is the raw configured symbol."""
    ctx = _Ctx("BTC")   # holds BTC, configured kPEPE -> not held -> may emit
    out = scan.scan({"asset": "kPEPE"}, ctx)
    for sig in out:
        assert sig["asset"] == "kPEPE"
