"""A key emitted in a signal's `data` that the scanner's `signal_data_schema` does not declare is not
ignored — the scaffold refuses the whole candidate (`delivery_candidate_invalid`: "data has unknown
key 'x' (not in signal_data_schema)"). The scanner still ticks and still logs a healthy EMIT, so the
strategy reads as live and alive while nothing it produces ever reaches the venue. Seen in October on
an authored package carrying `leverage` in `data` without declaring it.

Declaring the key is the fix, not removing it: a shipped scanner carries `leverage` in `data` as part
of its audit trail and declares it alongside the other fifteen fields."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from validate_strategy import undeclared_signal_data_offenders  # noqa: E402

_EMIT = '''
def scan(inputs, ctx):
    return [{
        "asset": "BTC", "direction": "LONG", "marginPct": 10, "leverage": 5,
        "data": {"score": 7, "leverage": 5},
    }]
'''


class UndeclaredDataKeyIsRefusedByTheScaffold(unittest.TestCase):
    def test_an_undeclared_key_is_reported(self):
        assert undeclared_signal_data_offenders(_EMIT, {"score": {"type": "number"}}) == ["leverage"]

    def test_declaring_it_clears_it(self):
        """The shipped pattern: `leverage` in `data` is fine once the schema declares it."""
        schema = {"score": {"type": "number"}, "leverage": {"type": "number"}}
        assert undeclared_signal_data_offenders(_EMIT, schema) == []

    def test_a_data_built_elsewhere_is_left_alone(self):
        """`data` from a variable or comprehension is unreadable here — skipped, never guessed at."""
        src = 'def scan(i, c):\n    payload = {"score": 1}\n    return [{"asset": "BTC", "data": payload}]\n'
        assert undeclared_signal_data_offenders(src, {"nothing": {"type": "number"}}) == []

    def test_unparseable_source_is_not_an_accusation(self):
        assert undeclared_signal_data_offenders("def scan(:\n", {"a": {"type": "number"}}) == []


class ShippedPackagesStayClean(unittest.TestCase):
    def test_no_shipped_scanner_is_flagged(self):
        """A check that refuses a working strategy is worse than no check. Pairing is by ENTRYPOINT:
        barracuda ships two external_scanners on one `path` (scan.py + close_all.py), and comparing a
        scanner against its sibling's schema invents offenders for every key it declares."""
        import yaml
        root = Path(__file__).resolve().parent.parent.parent / "strategies"
        if not root.is_dir():
            self.skipTest("strategies/ not present")
        bad, pairs = [], 0
        for rt in root.rglob("runtime.yaml"):
            try:
                doc = yaml.safe_load(rt.read_text())
            except Exception:
                continue
            if not isinstance(doc, dict):
                continue
            for sc in (doc.get("scanners") or []):
                if not isinstance(sc, dict) or sc.get("type") != "external_scanner":
                    continue
                schema = sc.get("signal_data_schema") or {}
                for f in rt.parent.rglob("scanners/" + (sc.get("entrypoint") or "scan.py")):
                    pairs += 1
                    for key in undeclared_signal_data_offenders(f.read_text(), schema):
                        bad.append(f"{f.relative_to(root)} [{sc.get('name')}] -> {key}")
        assert pairs > 100, f"only {pairs} scanner/schema pairs seen — the sweep stopped finding them"
        assert not bad, "shipped scanners flagged:\n  " + "\n  ".join(sorted(bad)[:20])


if __name__ == "__main__":
    unittest.main()
