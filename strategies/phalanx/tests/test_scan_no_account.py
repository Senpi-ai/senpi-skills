"""An unreadable account skips the tick and persists it like every other skip path.
The call on that path used to name keyword arguments _persist_state does not have, so a
wallet whose clearinghouse read failed raised TypeError on every tick instead of waiting."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "main", "scanners"))

import scan  # noqa: E402

_WALLET = "0x" + "0" * 40


class _State:
    def __init__(self):
        self.records = []

    def last(self):
        return {"cohort": {"addresses": [_WALLET], "refreshed_at": 1.0},
                "prev_tilts": {"BTC": {"long_n": 12, "short_n": 1}}}

    def append(self, rec):
        self.records.append(rec)


def test_no_account_tick_persists_and_returns_nothing(monkeypatch):
    monkeypatch.setattr(scan, "_get_account", lambda ctx: (0.0, []))
    st = _State()
    ctx = types.SimpleNamespace(state=st, wallet=_WALLET, senpi_mcp=None)
    assert scan.scan({}, ctx) == []
    assert st.records and st.records[-1]["result"]["gate"] == "no_account"
    # the cached cohort and the baseline survive the skipped tick
    assert st.records[-1]["cohort"]["addresses"] == [_WALLET]
    assert st.records[-1]["prev_tilts"] == {"BTC": {"long_n": 12, "short_n": 1}}
