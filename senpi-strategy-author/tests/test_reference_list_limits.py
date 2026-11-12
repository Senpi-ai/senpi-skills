"""The build guide states the MCP row limits an authored scanner depends on.

`discovery_get_top_traders` and `leaderboard_get_top` return 20 rows when `limit` is omitted. A
scanner written from "they return the whole board" builds its cohort from 20 traders and nothing
errors. The maker window is capped at 30 s by the MCP server when the taker fallback is on, so a
reference or template that states 45 s or 60 s for that case teaches a window that never runs. The
engine's own 45 s applies only without the taker fallback.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REFS = os.path.join(HERE, "..", "references")


def _read(path):
    return open(path, encoding="utf-8").read()


def test_the_build_guide_says_ranked_lists_need_an_explicit_limit():
    text = _read(os.path.join(REFS, "creating-a-strategy.md"))
    assert "return the whole board" not in text
    assert "`leaderboard_get_top` return 20 rows" in text
    assert "pages with `offset`" in text
    assert "`leaderboard_get_top` takes up to 500 and has no `offset`" in text


def test_the_skill_says_the_cohort_read_pages():
    text = _read(os.path.join(HERE, "..", "SKILL.md"))
    assert "20 rows unless you pass `limit`, page with `offset`" in text


def test_the_maker_window_default_is_the_server_cap():
    text = _read(os.path.join(REFS, "dsl-configuration.md"))
    row = next(l for l in text.splitlines() if l.startswith("| `fee_optimized_limit_options.execution_timeout_seconds`"))
    assert "| `30` with the taker fallback, else 45 |" in row
    assert "caps it at 30" in row


def test_the_build_guide_templates_stay_inside_the_server_cap():
    text = _read(os.path.join(REFS, "creating-a-strategy.md"))
    assert "execution_timeout_seconds: 60" not in text
    assert "execution_timeout_seconds: 45" not in text
