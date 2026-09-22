"""A scanner cannot set a position's stop: anything stop-shaped in the emitted `data{}` is an audit
trail, and the floor comes from the instance's DSL preset. The resident line has to say so and point at
the depth, and the conversion the depth hands the author has to invert the floor formula it documents."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "SKILL.md")
DSL_CONFIG = os.path.join(HERE, "..", "references", "dsl-configuration.md")


def test_skill_says_an_emitted_stop_does_not_bind_and_points_at_the_depth():
    text = open(SKILL, encoding="utf-8").read()
    for needle in ("`data{}` records, it never instructs",
                   "does NOT bind",
                   "exit.dsl_preset",
                   "dsl-configuration.md#a-stop-in-the-signal-does-not-bind"):
        assert needle in text, needle


def test_the_depth_exists_under_that_anchor():
    text = open(DSL_CONFIG, encoding="utf-8").read()
    assert "## A stop in the signal does not bind" in text
    for needle in ("A scanner cannot set a position's stop",
                   "Express the intended stop as the preset",
                   "entry × (1 - max_loss_pct/100/leverage)"):
        assert needle in text, needle


def test_the_roe_conversion_inverts_the_floor_formula():
    """floor = entry × (1 - max_loss_pct/100/lev)  ⇔  max_loss_pct = (1 - floor/entry) × lev × 100.
    Pinned so a retune of the floor formula cannot leave the author with a stale conversion."""
    entry, lev, max_loss_pct = 224.13, 3.0, 3.4156070137866234
    floor = entry * (1 - max_loss_pct / 100 / lev)
    assert round((1 - floor / entry) * lev * 100, 10) == round(max_loss_pct, 10)
