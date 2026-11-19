"""Regression proof: an incomplete 40-week trend window must not authorize a buy."""
import pytest
import test_chipmunk as fixtures


@pytest.mark.parametrize("name", ["rung1", "rung2", "rung3"])
def test_missing_interior_week_does_not_substitute_older_week(monkeypatch, name):
    rung = fixtures.RUNGS[name]
    close = 100.0 - rung.inputs["depthPct"] - 1.0
    weekly = [close - 40.0] * 12 + [close] * 40
    weekly[25] = close + 10.0

    class MissingInteriorWeek(fixtures._Wallet):
        def call_tool(self, tool, args):
            payload = super().call_tool(tool, args)
            if tool == "market_get_asset_data" and args["asset"] == "BTC":
                # Permitted feed shape: the runtime drops an uncastable candle row.
                # Leave the newest closed week intact, so freshness alone passes.
                payload["data"]["candles"]["1w"].pop(25)
            return payload

    kwargs = {"daily": {"BTC": fixtures._dip(rung.inputs["depthPct"] + 1)},
              "weekly": {"BTC": weekly}}
    intact, measured = fixtures._scan(monkeypatch, fixtures._Wallet(**kwargs), rung=rung)
    assert intact == []
    assert measured["measured"]["BTC"]["regime_average"] == pytest.approx(close + 0.25)

    gap, measured = fixtures._scan(monkeypatch, MissingInteriorWeek(**kwargs), rung=rung)
    assert gap == [], (
        f"{name}: incomplete weekly window authorized BTC at {close}; "
        f"actual intact average={close + 0.25}, substituted average="
        f"{measured['measured']['BTC']['regime_average']}"
    )


def _weeks(closes, start_ms=1_700_000_000_000):
    week_ms = 7 * 86400 * 1000
    return [{"t": start_ms + i * week_ms, "c": str(c)} for i, c in enumerate(closes)]


def test_weekly_average_needs_every_week_of_its_window():
    average = fixtures.R1.scoring.weekly_average
    rows = _weeks([10.0] * 5 + [20.0] * 40)
    assert average(rows, 40) == pytest.approx(20.0)
    assert average(rows[:2] + rows[3:], 40) == pytest.approx(20.0)   # a gap older than the window is fine
    assert average(rows[:30] + rows[31:], 40) is None                 # a week missing inside the window
    assert average(rows + [dict(rows[-1])], 40) is None               # the newest week twice
    assert average(rows[-39:], 40) is None                            # too new for the window
