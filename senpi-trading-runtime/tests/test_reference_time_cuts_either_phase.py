"""The runtime's preset-root time cuts have no phase guard (dsl-reference: `hard_timeout` /
`weak_peak_cut` / `dead_weight_cut` "have no phase guard and still fire"; the runtime fixed the same
sentence in its own guide and pinned it in guide-commands.test.ts); the concepts reference must not tell
an author that entering Phase 2 disarms `hard_timeout`."""
import os

REF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "runtime-concepts.md")


def test_hard_timeout_is_documented_as_either_phase():
    text = open(REF, encoding="utf-8").read()
    assert "`hard_timeout` — either phase" in text
    assert "not disarm `hard_timeout`" in text
    assert "hard_timeout` is Phase 1 only" not in text
    assert "Phase 1 only. |" not in text
