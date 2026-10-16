"""The author skill must not tell the agent to fall back to committed capital.

SKILL.md used to read: `total_in_hyperliquid` (fall back to `total_withdrawable`).

Both halves of that are wrong in the same direction:

  * `total_withdrawable` is free margin sitting INSIDE strategy wallets. It is already committed to
    a strategy; spending it means withdrawing from one first, which is the user's decision.
  * a `total_in_hyperliquid` of **0** is falsy, so the fallback fires exactly when the user has
    nothing free — the worst possible moment to substitute a bigger number.

On this repo's own portfolio fixture that fallback yields **$2,461.98** against a truly free balance
of **$1.51**. On a real account on 2026-10-02 a catalog offer quoted "~$6,470 free" when $21.06 was
free and the remainder was live in seven strategies.

`senpi-portfolio/scripts/portfolio.py` already documents this as the balance-bucket trap and
`senpi-strategy-ops/references/lifecycle.md` already states the waterfall for deploy preflight.
This pins the author skill to the same rule so three surfaces cannot disagree again.

Run: python3 -m pytest senpi-strategy-author/tests/test_balance_rule_excludes_committed_capital.py -q
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SKILL = os.path.join(ROOT, "senpi-strategy-author", "SKILL.md")


def _skill():
    with open(SKILL, encoding="utf-8") as fh:
        return fh.read()


def test_the_withdrawable_fallback_is_gone():
    """Every mention of falling back to `total_withdrawable` must be a PROHIBITION, not an
    instruction. The naive check ("does this phrase appear?") fails on the prohibition itself, so
    this looks at what precedes each occurrence."""
    src = _skill()
    offenders = []
    for m in re.finditer(r"fall\s*back\s*to\s*\n?\**`?total_withdrawable", src, re.I):
        lead = src[max(0, m.start() - 24):m.start()].lower()
        if not re.search(r"never|not |don'?t|must not", lead):
            offenders.append(src[max(0, m.start() - 60):m.end() + 20].replace("\n", " "))
    assert not offenders, (
        "SKILL.md tells the agent to fall back to `total_withdrawable` — free margin inside "
        "strategy wallets. That is capital the user has already committed; offering it as a budget "
        "offers to fund a new strategy with money that is already working. Found: " + str(offenders))


def test_the_deployable_waterfall_is_named():
    """Naming the three fields is the whole fix — 'check the balance' is what produced the bug."""
    src = _skill()
    for field in ("total_in_hyperliquid", "total_spot_usd_in_hyperliquid", "token_balances"):
        assert field in src, (
            f"SKILL.md no longer names `{field}` as part of the deployable balance. The agent needs "
            f"the three buckets spelled out, not 'read the balance'.")


def test_the_committed_buckets_are_named_as_off_limits():
    src = _skill()
    for field in ("total_withdrawable", "total_balance_usd"):
        assert field in src, (
            f"SKILL.md must name `{field}` explicitly as NOT a budget. Omitting it is how the "
            f"agent reaches for it — it is the largest number in the response.")
    for field in ("total_withdrawable", "total_balance_usd"):
        negated = any(
            re.search(r"never|not |don'?t|must not", src[max(0, m.start() - 60):m.start()], re.I)
            for m in re.finditer(re.escape(field), src))
        assert negated, (
            f"`{field}` is mentioned but never negated. Naming a field without forbidding it reads "
            f"as permission — and it is the biggest number in the response.")


def test_zero_is_documented_as_a_real_balance():
    """The mechanism, not just the symptom: an `or` chain turns a legitimate 0 into a fallback."""
    src = _skill()
    assert re.search(r"\b0 is a fact\b|0 is a FACT|not a missing read", src, re.I), (
        "SKILL.md does not say that a balance of 0 is a fact rather than a failed read. Without "
        "that, any future `or`/fallback chain reintroduces the bug — 0 is falsy.")


def test_the_user_is_told_where_the_committed_money_is():
    """Reporting "$21 free" alone is accurate but unhelpful; the user wants to know about the rest,
    and only they can decide to pull it out of a running strategy."""
    src = _skill()
    assert "Never say" in src and "free" in src, "no say/never-say guidance on reporting balances"
    assert re.search(r"withdraw(ing)? from a strategy|close or withdraw", src, re.I), (
        "SKILL.md does not tell the agent to name the strategy the money would have to come out "
        "of. Freeing committed capital is the user's call, not a silent substitution.")
