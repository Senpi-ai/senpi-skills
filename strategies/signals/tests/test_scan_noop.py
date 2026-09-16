"""The signals host: a tick READS through ctx.senpi_mcp (so `senpi validate` can prove it — a
tick that reads nothing is UNPROVEN), writes signals.md + current.json + the ring, persists its
numbers, and returns [] — always. Run: python3 -m pytest strategies/signals/tests -q"""
import importlib.util
import pathlib
import sys
import types

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
PKG = ROOT / "strategies" / "signals"
sys.path.insert(0, str(PKG / "main" / "scanners"))            # `import scoring` (sibling model)
sys.path.insert(0, str(ROOT / "senpi-signals" / "tests"))     # the fixture shapes (test_sweep moved to tests/)
from test_sweep import fake_call_tool  # noqa: E402

_spec = importlib.util.spec_from_file_location("signals_scan", PKG / "main" / "scanners" / "scan.py")
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


class _State:
    def __init__(self):
        self.records = []

    def last(self):
        return self.records[-1] if self.records else None

    def append(self, rec):
        self.records.append(rec)


def _ctx(call_tool, state, dry_run):
    return types.SimpleNamespace(senpi_mcp=types.SimpleNamespace(call_tool=call_tool), state=state,
                                 wallet="0x" + "0" * 40, dry_run=dry_run, scanner_name="signals_sweep",
                                 interval_seconds=2700)


def test_tick_reads_writes_the_feed_and_emits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("SENPI_STATE_DIR", str(tmp_path))
    calls = []

    def call_tool(name, args):
        calls.append(name)
        return fake_call_tool(name, args)

    st = _State()
    assert scan.scan({"consumer": "social", "universeTopN": 120}, _ctx(call_tool, st, dry_run=True)) == []
    assert calls[0] == "market_list_instruments" and len(calls) == 6     # read through ctx.senpi_mcp → provable
    out = tmp_path / "signals"
    assert (out / "signals.md").is_file() and (out / "current.json").is_file() and (out / "state.json").is_file()
    rec = st.records[-1]
    assert rec["reads"] == 6 and rec["assets"] == 4 and rec["coverage"]["cohort"].startswith("ok")


def test_a_dead_service_is_a_logged_tick_not_a_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SENPI_STATE_DIR", str(tmp_path))
    st = _State()
    out = scan.scan({}, _ctx(lambda n, a: fake_call_tool(n, a, fail=("discovery_get_top_traders",)), st, False))
    assert out == [] and st.records[-1]["coverage"]["cohort"].startswith("NO DATA")
    assert "coverage cohort: NO DATA" in capsys.readouterr().err


def test_missing_skill_returns_nothing_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(scan, "_sweep", lambda: None)
    assert scan.scan({}, _ctx(None, None, True)) == []
    assert "sweep.py not found" in capsys.readouterr().err


def test_runtime_yaml_cannot_trade():
    rt = yaml.safe_load((PKG / "main" / "runtime.yaml").read_text())
    assert rt["strategy"]["slots"] == 0
    ext = [s for s in rt["scanners"] if s["type"] == "external_scanner"]
    assert [s["name"] for s in ext] == ["signals_sweep"] and ext[0]["interval_seconds"] == 2700
    assert ext[0]["timeout_seconds"] < ext[0]["interval_seconds"]
    opens = [a for a in rt["actions"] if a["action_type"] == "OPEN_POSITION"]
    assert len(opens) == 1 and opens[0]["scanners"] == ["signals_sweep"]
    src = (PKG / "main" / "scanners" / "scan.py").read_text()
    assert "return []" in src and '"direction"' not in src         # nothing that could be a signal


def test_in_the_runtime_the_feed_lands_beside_the_runtimes_own_state(tmp_path, monkeypatch):
    # the scanner child gets no SENPI_STATE_DIR: the root is read off the launch config path
    # (<root>/<runtime>-<wallet>/scanner-launch/<scanner>.launch.json), so the three files land in
    # <root>/signals/ — the directory the content automation reads
    monkeypatch.delenv("SENPI_STATE_DIR", raising=False)
    monkeypatch.delenv("SENPI_SIGNALS_STATE", raising=False)
    cfg = tmp_path / "senpi-state" / "signals-main-0xabc" / "scanner-launch" / "signals_sweep.launch.json"
    monkeypatch.setattr(sys, "argv", ["scaffold", str(cfg)])
    st = _State()
    assert scan.scan({}, _ctx(fake_call_tool, st, False)) == []
    out = tmp_path / "senpi-state" / "signals"
    assert (out / "signals.md").is_file() and (out / "current.json").is_file() and (out / "state.json").is_file()
    assert scan.scoring.runtime_state_root(["python"]) is None                 # outside the runtime: env/defaults
