"""
Shared test fixtures for TIGER test suite.

Design principle: expected values in tests are derived from mathematical
definitions and business rules, NOT from running the code. If a test fails,
it indicates a code bug, not a test issue.
"""

import sys
import os
import json
import copy
import importlib.util
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ─── Path Setup ──────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tiger_config
import tiger_lib
import roar_config


def import_script(name, filename):
    """Import a hyphenated script file as a module."""
    spec = importlib.util.spec_from_file_location(name, str(SCRIPTS_DIR / filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture
def sample_config():
    """Minimal valid TIGER config as AliasDict."""
    return tiger_config._to_alias_dict(tiger_config.deep_merge(
        tiger_config.DEFAULT_CONFIG,
        {
            "strategyId": "test-strategy",
            "strategyWallet": "0xTEST123",
            "budget": 1000,
            "target": 2000,
            "deadlineDays": 7,
            "telegramChatId": "12345",
        }
    ))


@pytest.fixture
def sample_state():
    """Minimal valid TIGER state as AliasDict."""
    return tiger_config._to_alias_dict(tiger_config.deep_merge(
        tiger_config.DEFAULT_STATE,
        {
            "instanceKey": "test-strategy",
            "currentBalance": 1000,
            "peakBalance": 1000,
            "dayStartBalance": 1000,
            "aggression": "NORMAL",
            "activePositions": {},
            "safety": {"halted": False, "haltReason": None},
        }
    ))


@pytest.fixture
def tmp_runtime(tmp_path):
    """TigerRuntime backed by temp directories."""
    workspace = tmp_path / "workspace"
    state_dir = workspace / "state"
    instance_dir = state_dir / "test-strategy"
    instance_dir.mkdir(parents=True)

    config_data = tiger_config.deep_merge(tiger_config.DEFAULT_CONFIG, {
        "strategyId": "test-strategy",
        "strategyWallet": "0xTEST123",
        "budget": 1000,
        "target": 2000,
    })
    config_file = workspace / "tiger-config.json"
    config_file.write_text(json.dumps(config_data))

    return tiger_config.TigerRuntime(
        workspace=str(workspace),
        scripts_dir=str(workspace / "scripts"),
        state_dir=str(state_dir),
        config_file=str(config_file),
        source="test",
    )


@pytest.fixture
def mock_deps(tmp_runtime):
    """Full deps dict with mocked externals backed by temp dirs."""
    captured = []

    deps = tiger_config.resolve_dependencies(
        runtime=tmp_runtime,
        overrides={
            "output": lambda payload: captured.append(payload),
            "get_all_instruments": lambda: SAMPLE_INSTRUMENTS,
            "get_asset_candles": lambda asset, intervals=None: {"error": "not mocked"},
            "get_prices": lambda assets=None: {},
            "get_sm_markets": lambda limit=50: [],
            "get_clearinghouse": lambda wallet: {"marginSummary": {"accountValue": "1000"}, "assetPositions": []},
            "close_position": lambda wallet, coin, reason="": {"success": True},
            "edit_position": lambda wallet, coin, **kw: {"success": True},
        }
    )
    deps["_captured_output"] = captured
    return deps


# ─── Sample Data ─────────────────────────────────────────────

SAMPLE_INSTRUMENTS = [
    {
        "name": "BTC", "max_leverage": 50, "is_delisted": False,
        "context": {
            "dayNtlVlm": "500000000", "openInterest": "100000",
            "funding": "0.0001", "markPx": "67500", "prevDayPx": "66000",
        }
    },
    {
        "name": "ETH", "max_leverage": 50, "is_delisted": False,
        "context": {
            "dayNtlVlm": "200000000", "openInterest": "50000",
            "funding": "0.00005", "markPx": "3500", "prevDayPx": "3450",
        }
    },
    {
        "name": "SOL", "max_leverage": 20, "is_delisted": False,
        "context": {
            "dayNtlVlm": "100000000", "openInterest": "30000",
            "funding": "-0.0002", "markPx": "150", "prevDayPx": "145",
        }
    },
    {
        "name": "DOGE", "max_leverage": 10, "is_delisted": False,
        "context": {
            "dayNtlVlm": "50000000", "openInterest": "20000",
            "funding": "0.0003", "markPx": "0.15", "prevDayPx": "0.14",
        }
    },
    {
        "name": "LOWLEV", "max_leverage": 2, "is_delisted": False,
        "context": {
            "dayNtlVlm": "10000000", "openInterest": "5000",
            "funding": "0", "markPx": "10", "prevDayPx": "10",
        }
    },
    {
        "name": "DEAD", "max_leverage": 20, "is_delisted": True,
        "context": {
            "dayNtlVlm": "1000000", "openInterest": "100",
            "funding": "0", "markPx": "1", "prevDayPx": "1",
        }
    },
]


def make_candles(closes, highs=None, lows=None, opens=None, volumes=None):
    """Build candle dicts from explicit price arrays.

    This is the primary candle builder. Callers supply exact values
    so expected indicator outputs can be hand-computed.
    """
    n = len(closes)
    if highs is None:
        highs = [c * 1.005 for c in closes]
    if lows is None:
        lows = [c * 0.995 for c in closes]
    if opens is None:
        opens = list(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    return [
        {"t": 1000000 + i * 3600000, "o": opens[i], "h": highs[i], "l": lows[i], "c": closes[i], "v": volumes[i]}
        for i in range(n)
    ]
