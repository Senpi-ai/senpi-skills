# A stop in the signal does not bind

A scanner cannot set a position's stop. The emitted signal's `data{}` is an **audit trail**: a
`stop_price`, `stop`, or `take_profit` computed in `scan()` is recorded there and nothing acts on it. When
the position opens, the DSL installs its own Phase-1 floor from that instance's `exit.dsl_preset` —
`entry × (1 - max_loss_pct/100/leverage)` ([dsl-configuration.md](dsl-configuration.md)) — and that is the only
stop the position has.

This is quiet in the worst way: the signal, the scanner's `latest.json` and the author's own reasoning all
carry the tighter structural level, while the live position sits behind the preset's wider one. A scanner
computing a retest low at 1.1% under entry on a 3× sleeve is authoring a 3.4% ROE stop; a `max_loss_pct: 6`
preset gives the position 6% — nearly double the intended risk, with nothing in the logs saying so.

**Express the intended stop as the preset.** Convert the level to ROE at the instance's leverage and set
the preset's `max_loss_pct` to it — the one field you change (shipped presets run 5–18, so none matches as-is):

```
max_loss_pct = (1 - stop_price / entry_price) × leverage × 100     # LONG
max_loss_pct = (stop_price / entry_price - 1) × leverage × 100     # SHORT
```

A structural level that moves per signal cannot be expressed this way at all — the preset is per instance,
not per entry. Size the preset to the widest stop the thesis tolerates and say so to the user, rather than
emitting a per-signal level that will not be honoured. Keep the level in `data{}` afterwards if you want it
in the audit trail; never as the mechanism.
