"""Needle guards on the money-rails rules in SKILL.md.

A FAILED `strategy_top_up` can leave the money in the funding wallet's SPOT balance (the worker's
first leg ran, its second did not) while the status message says nothing moved. The skill must make
the agent locate the money, move it back with `transfer_spot_to_perps`, say which balance it is in,
and never re-submit in a loop. The same file guards the perps precheck before a top-up, the
`details.available` retry on a cents-short withdrawal, "everything" as the exact available figure,
and the creation-fee sentence — each a rule a rewrite could quietly drop.

Run:  python3 -m pytest senpi-deposit-withdraw-transfer/tests -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "SKILL.md")
README = os.path.join(HERE, "..", "..", "README.md")


def _skill():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def _needles(*needles):
    text = _skill()
    for needle in needles:
        assert needle in text, needle


def test_failed_top_up_recovers_the_spot_leg_and_names_the_balance():
    _needles("perps → Spot on the funding wallet, then Spot → the strategy wallet",
             "when the first leg *did* run: treat it as a hint",
             "the money is in the funding wallet's **Spot**",
             "Move it back with `transfer_spot_to_perps` for that amount",
             "Do **not** re-submit on your own.",
             "**Never say \"still in the funding wallet\" without naming the balance — perps or Spot.**")


def test_failed_top_up_reread_bypasses_the_portfolio_cache():
    """The default account_get_portfolio read is cached — a post-FAILED re-read that does not
    force-fetch cannot see the Spot rise, and the money is reported as never having moved."""
    text = _skill()
    assert text.count("`forceFetch: true`") >= 2, "both the precheck and the FAILED re-read must force-fetch"


def test_at_most_one_resubmit_and_never_the_same_strategy_twice():
    _needles("**At most one re-submit — only if the user asks, and never for the same strategy twice.**",
             "submit once with a **new** idempotency key",
             "something is wrong that a retry won't fix",
             "Senpi support with the top-up request id",
             "Never a third submit, never a retry loop")


def test_a_second_failed_top_up_goes_to_support_never_to_a_fresh_deploy():
    """A second FAILED is a support case. A fresh deploy fixes nothing a retry didn't: it only charges
    another wallet-creation fee and leaves the old strategy running beside the new one."""
    _needles("**Never offer a fresh deploy as the fix for a failed top-up:**",
             "a fresh deploy offered as the fix for a failed top-up")
    text = _skill()
    for defect in ("cannot receive top-ups right now",
                   "can't receive top-ups right now",
                   "a fresh deploy gets a wallet",
                   "deploy it fresh (new wallet)",
                   "fresh so it gets a new wallet"):
        assert defect not in text, f"the fresh-deploy remedy came back: {defect!r}"


def test_perps_precheck_gates_on_withdrawable_never_on_account_value():
    """total_in_hyperliquid includes margin locked in open positions, so it over-states what a top-up can
    draw. The gate is the funding wallet's `withdrawable`; account value is an upper bound only."""
    _needles("**Free perps USDC — the gate:** `withdrawable` on the `main` side of `strategy_get_clearinghouse_state`",
             "`walletType: embedded` entry in `user_get_me` — read it to\n     check the balance, **never** to hand out as a deposit address",
             "It is an **upper bound only, never\n   the gate**",
             "balance fields sit under `data.portfolio`",
             "`total_spot_usd_in_hyperliquid`",
             "**The amount must not exceed free perps (`withdrawable`)**",
             "\"Your funding wallet has $X free in perps; topping up $Y.\"")


def test_account_value_is_never_presented_as_the_free_perps_figure():
    """The defect this file once pinned: the precheck named `total_in_hyperliquid` as the funding wallet's
    perps USDC. A wallet with an open position then passes the precheck and the top-up FAILS."""
    text = _skill()
    for defect in ("perps USDC is `total_in_hyperliquid`",
                   "**The amount must not exceed the perps figure**",
                   "\"Your funding wallet holds $X in perps; topping up $Y.\""):
        assert defect not in text, f"the account-value precheck came back: {defect!r}"


def test_top_up_is_polled_never_resubmitted_while_pending():
    _needles("`data.top_up_request.id`",
             "poll `strategy_get_top_up_status`",
             "Re-submitting while PENDING is how strategies get double-funded")


def test_withdrawal_short_by_cents_retries_once_with_details_available():
    _needles("`details.available`: retry ONCE with exactly that figure. Never ask the user for a new number.")


def test_withdraw_everything_is_the_exact_available_figure():
    _needles("means the exact available figure from the",
             "never a rounded amount",
             "keeps scanning — offer to close it")


def test_creation_fee_is_stated_before_money_moves():
    _needles("Creating a strategy reserves a creation fee — about $1, budgeted as $1.50 per wallet",
             "the per-wallet minimum and fee up front",
             "\"funds in transit\" figure",
             "Never invent a fee a tool did not return")


def test_say_never_say_table_is_present():
    _needles("| Say | Never say |")


def test_existing_rails_survive():
    """The new sections extend the skill; the two iron rules and their tool names stay verbatim."""
    _needles("`widget_type: \"fund_user_wallet\"`",
             "I can't send funds outside of Senpi for you",
             "`strategy_top_up`", "`strategy_withdraw_funds`", "`transfer_spot_to_perps`",
             "`strategy_get_clearinghouse_state`", "`account_get_portfolio`")


def test_readme_row_matches_the_skill_version():
    m = re.search(r'^  version: "(\d+\.\d+\.\d+)"', _skill(), re.M)
    assert m, "SKILL.md frontmatter version not found"
    with open(README, encoding="utf-8") as f:
        readme = f.read()
    row = re.search(r"\| \[`senpi-deposit-withdraw-transfer`\]\([^)]+\) \| (\d+\.\d+\.\d+) \|", readme)
    assert row, "README row for senpi-deposit-withdraw-transfer not found"
    assert row.group(1) == m.group(1), f"README says {row.group(1)}; SKILL.md says {m.group(1)}"


def test_public_repo_hygiene():
    """Rules only: no user ids, no wallet addresses, no incident narrative in a public skill."""
    text = _skill()
    assert not re.search(r"\bM\d{5,}\b", text), "a user id leaked into the skill text"
    assert not re.search(r"0x[0-9a-fA-F]{40}", text), "a wallet address leaked into the skill text"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")
