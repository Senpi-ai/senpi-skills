"""Catalog-wide guard: a scanner must never emit an optional `data` key whose value is None.

WHY THIS EXISTS (2026-09-04). `signal_data_schema` marks a key `required: false`, which permits
the key to be **ABSENT** — not present-and-null. The intake validates the emitted envelope and
discards the WHOLE candidate on a null:

    delivery_candidate_invalid: data key 'persistenceHours' has wrong type
                                (expected number, got NoneType)

The scanner logs a normal `candidate_rejected` tick, so every error-keyed health check passes while
the strategy never trades. Measured cost on 2026-09-04: M409673's catalog `dog` discarded **151**
candidates over 7 days and had not traded in 15 days ($70 ACTIVE); `vulture` users lost 71 + 43 + 11
more, `grizzly` 1. Three catalog packages emit a nullable optional key when the upstream
funding-persistence read has no row for the asset — the common real-world case.

Run:
  python3 -m pytest strategies/tests -q
  python3 strategies/tests/test_no_null_optional_signal_keys.py
"""
import glob
import os
import re
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Optional keys observed to arrive as None from an upstream read that has no row for the asset.
# Any scanner emitting one of these must drop it when it is None rather than emit a null.
_NULLABLE_OPTIONAL_KEYS = (
    "persistenceHours",
    "fundingPersistenceHours",
    "crowdingTrend",
)


def _load(pkg, instance="main"):
    """Import a package's scanner modules with its own scanners/ dir first on sys.path."""
    d = os.path.join(_ROOT, pkg, instance, "scanners")
    sys.path.insert(0, d)
    try:
        for mod in ("scoring", "scan"):
            sys.modules.pop(mod, None)
        import scoring  # noqa: F401
        import scan
        return scan
    finally:
        sys.path.remove(d)


class _FakeState:
    def __init__(self):
        self.rows = []

    def append(self, row):
        self.rows.append(row)

    def last(self):
        return self.rows[-1] if self.rows else None

    def recent(self, n):
        return self.rows[-n:]


class _FakeMcp:
    """Canned MCP reads. `market_get_funding_history` returns a row WITHOUT
    `persistence_hours` — exactly what the live API returns for an asset it has no
    funding-persistence history for, and the trigger for the null emission."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if name not in self.responses:
            raise AssertionError(f"unexpected tool call {name!r}")
        return self.responses[name]


class _FakeCtx:
    def __init__(self, responses, wallet="0x" + "ab" * 20):
        self.senpi_mcp = _FakeMcp(responses)
        self.wallet = wallet
        self.state = _FakeState()


def _dog_ctx():
    """One BTC market that clears dog's hard gates (30+ traders, 4h exhaustion >= 3%
    aligned with SM direction, 15m velocity > 0), with NO funding-persistence row."""
    market = {
        "token": "BTC",
        "dex": "",
        "direction": "LONG",
        "pct_of_top_traders_gain": 20.0,
        "trader_count": 120,
        "token_price_change_pct_4h": 5.0,
        "token_price_change_pct_1h": 0.5,
        "contribution_pct_change_15m": 1.0,
        "contribution_pct_change_1h": 0.5,
        "contribution_pct_change_4h": 0.5,
    }
    return _FakeCtx({
        "strategy_get_clearinghouse_state": {
            "data": {"main": {"marginSummary": {"accountValue": "1000"}, "assetPositions": []}}
        },
        "leaderboard_get_markets": {"data": {"markets": [market]}},
        "market_get_funding_regime": {"data": {"regime": "LONG_CROWDED"}},
        # row present, persistence_hours ABSENT -> scoring resolves it to None
        "market_get_funding_history": {"data": {"data": [{"asset": "BTC"}]}},
        "market_get_asset_data": {"data": {"asset_context": {"funding": 0.001}}},
    })


def test_dog_emits_no_null_data_values_when_funding_persistence_is_missing():
    """The M409673 defect, end to end: scan() must emit a candidate whose `data` carries
    no None. Before the fix `persistenceHours` and `crowdingTrend` are both None and the
    intake discards the candidate."""
    scan = _load("dog")
    signals = scan.scan({"minScore": 0}, _dog_ctx())

    assert signals, "fixture must produce at least one emitted signal"
    for sig in signals:
        nulls = sorted(k for k, v in sig["data"].items() if v is None)
        assert not nulls, f"emitted null optional keys {nulls} — intake discards this candidate"


def test_dog_keeps_the_optional_key_when_the_value_is_real():
    """The fix must drop only nulls; a genuine persistence reading still ships."""
    scan = _load("dog")
    ctx = _dog_ctx()
    ctx.senpi_mcp.responses["market_get_funding_history"] = {
        "data": {"data": [{"asset": "BTC", "persistence_hours": 18, "funding_trend": "DECAYING"}]}
    }
    signals = scan.scan({"minScore": 0}, ctx)

    assert signals
    data = signals[0]["data"]
    assert data["persistenceHours"] == 18.0
    assert data["crowdingTrend"] == "DECREASING"


def _scanners_emitting_nullable_keys():
    out = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "*", "*", "scanners", "scan.py"))):
        src = open(path, encoding="utf-8").read()
        if any(f'"{k}"' in src for k in _NULLABLE_OPTIONAL_KEYS):
            out.append(os.path.relpath(path, os.path.dirname(_ROOT)))
    return out


@pytest.mark.parametrize("rel", _scanners_emitting_nullable_keys())
def test_every_scanner_emitting_a_nullable_optional_key_drops_nulls(rel):
    """Fleet guard: any scanner that emits one of the known-nullable optional keys must
    filter None out of the emitted `data` map. Catches the next package to hit this."""
    src = open(os.path.join(os.path.dirname(_ROOT), rel), encoding="utf-8").read()
    assert re.search(r"if v is not None", src), (
        f"{rel} emits a nullable optional key but never drops None from `data` — "
        "the intake will discard every such candidate"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
