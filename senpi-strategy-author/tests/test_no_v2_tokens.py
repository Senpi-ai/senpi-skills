#!/usr/bin/env python3
"""Drift guard: the author skill must teach Runtime 3.0 (`scan(inputs, ctx)`), never the retired
v2 producer-daemon model. Fails if any retired-model token is USED (not just named to forbid it)
in SKILL.md or references/*.md — so the skill can't silently drift back the way it did before the
3.0 migration (the C1 finding). Run: python3 senpi-strategy-author/tests/test_no_v2_tokens.py

# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # senpi-strategy-author/

# Tokens that belong ONLY to the retired v2 producer model. None should appear as *usage*.
RETIRED = [
    "senpi_runtime_helpers", "SenpiClient", "producer_daemon", "push_signal",
    "load_params(", "POST /signals", "-producer.py", "config.fields",
]
# A line may name a retired token to FORBID it (e.g. "there is no push_signal"). Allow the token
# only when the line also carries a negation/deprecation marker; flag it as usage otherwise.
NEG = ("no ", "not ", "never", "retired", "deprecat", "do not", "don't", "instead of",
       "replaced", "removed", "❌", "obsolete", "legacy", "v2 ")


def main() -> int:
    bad = []
    files = sorted(ROOT.glob("*.md")) + sorted(ROOT.glob("references/*.md"))
    for f in files:
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            low = line.lower()
            for tok in RETIRED:
                if tok in line and not any(n in low for n in NEG):
                    bad.append(f"{f.relative_to(ROOT)}:{i}: retired v2 token {tok!r} used → {line.strip()[:100]}")
    if bad:
        print("✗ author skill still teaches the retired v2 producer model (must be Runtime 3.0 scan(inputs, ctx)):")
        for b in bad:
            print("   ", b)
        return 1
    print(f"✓ author skill clean of retired v2 tokens across {len(files)} docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
