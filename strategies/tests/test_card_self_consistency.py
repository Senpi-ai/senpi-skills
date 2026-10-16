"""A catalog card must not contradict itself, or the code it describes.

`gen_catalog.py` puts `tagline`, `belief_plain` and `thesis` straight in front of a user through
discover, and they are written by hand at different times. On 2026-09-19 athena-x shipped with
`belief_plain` rewritten and the tagline left behind: the body said "about 3.6x the gross exposure"
while the tagline said "Roughly 3.7x", and the tagline still promised "the same entries" two lines
above the body explaining that two entry gates had been removed. The tagline is the field discover
shows FIRST, so the highest-visibility sentence was the wrong one.

Neither error is athena-x's alone in kind, so these run over every package:

  1. exposure multipliers quoted anywhere in one card must agree with each other;
  2. a card that claims its entries/gates match another package must actually have that package's
     `risk.guard_rails` keys.

Run: python3 -m pytest strategies/tests/test_card_self_consistency.py -q
"""
import os
import re

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
STRATEGIES = os.path.join(HERE, "..")
CARD_FIELDS = ("tagline", "belief_plain", "thesis")
EXPOSURE = re.compile(r"(\d+\.?\d*)x\s+(?:the\s+)?(?:gross\s+)?exposure", re.I)
SAMENESS = re.compile(r"same (?:signals?, )?(?:gates?|entries)|gates?[^.]{0,40}unchanged", re.I)
REMOVED = re.compile(r"no (?:daily loss halt|cap on entries)|entry gates are also gone|gates that ration", re.I)


def _packages():
    for name in sorted(os.listdir(STRATEGIES)):
        path = os.path.join(STRATEGIES, name, "strategy.yaml")
        if not os.path.isfile(path):
            continue
        doc = yaml.safe_load(open(path, encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("catalog"), dict):
            yield name, doc


def _card_text(doc):
    return {f: str(doc["catalog"].get(f) or "") for f in CARD_FIELDS}


@pytest.mark.parametrize("pkg,doc", list(_packages()), ids=lambda v: v if isinstance(v, str) else "")
def test_one_card_quotes_one_exposure_figure(pkg, doc):
    """3.6x in the body and 3.7x in the tagline is a card arguing with itself."""
    seen = {}
    for field, text in _card_text(doc).items():
        for m in EXPOSURE.finditer(text):
            seen.setdefault(round(float(m.group(1)), 1), []).append(field)
    assert len(seen) <= 1, (
        f"{pkg}: card quotes conflicting exposure figures {dict(sorted(seen.items()))}")


@pytest.mark.parametrize("pkg,doc", list(_packages()), ids=lambda v: v if isinstance(v, str) else "")
def test_a_card_does_not_promise_sameness_it_withdraws(pkg, doc):
    """The athena-x shape: 'the same entries' in one field, 'two entry gates are gone' in another."""
    card = _card_text(doc)
    claims = {f for f, t in card.items() if SAMENESS.search(t)}
    withdraws = {f for f, t in card.items() if REMOVED.search(t)}
    assert not (claims and withdraws), (
        f"{pkg}: {sorted(claims)} claim entry/gate sameness that {sorted(withdraws)} takes back")


def _rails(pkg, instance):
    p = os.path.join(STRATEGIES, pkg, instance, "runtime.yaml")
    return set((yaml.safe_load(open(p, encoding="utf-8"))["risk"]["guard_rails"] or {}))


def test_athena_x_card_matches_the_gates_it_actually_has():
    """It names Athena; a reader comparing the two must not be told the gates match when they do
    not. Both removals are deliberate — the card just has to say so."""
    ax, a = {}, {}
    for leg in ("phalanx", "aegis"):
        ax[leg], a[leg] = _rails("athena-x", leg), _rails("athena", leg)
    missing = {leg: sorted(a[leg] - ax[leg]) for leg in ax if a[leg] - ax[leg]}
    card = " ".join(_card_text(yaml.safe_load(
        open(os.path.join(STRATEGIES, "athena-x", "strategy.yaml"), encoding="utf-8"))).values())
    if missing:
        assert REMOVED.search(card), (
            f"athena-x drops guard rails {missing} but the card never says an entry gate is gone")
        assert not SAMENESS.search(card), (
            f"athena-x drops guard rails {missing} while its card claims entry/gate sameness")
