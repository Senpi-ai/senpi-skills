"""Money already inside a strategy is not a budget for a new one.

`account_get_portfolio` reports several totals and only three of them are deployable:

    total_in_hyperliquid           free USDC in the FUNDING wallet's perps account   DEPLOYABLE
    total_spot_usd_in_hyperliquid  ... its Spot balance                              DEPLOYABLE
    token_balances[]               ... EVM stables, bridgeable                       DEPLOYABLE
    total_withdrawable             free margin sitting INSIDE strategy wallets       COMMITTED
    total_allocated_in_strategy    margin backing open strategy positions            AT RISK
    total_balance_usd              the sum of every bucket above                     MEANINGLESS AS A BUDGET

`discover.fetch_user_context` used to read `total_balance_usd or total_in_hyperliquid`, so the
catalog offered the user money that was already working. Two demonstrations:

  * On this repo's own portfolio fixture that reads **$3,102.94** where the truly free balance is
    **$1.51** — the account had $3,101.43 live inside strategies.
  * On a real account on 2026-10-02 the catalog offer said "~$6,470 free" when **$21.06** was free
    and the rest was in seven running strategies. The agent then proposed funding a new one.

The same rule is already stated in `senpi-portfolio/scripts/portfolio.py` (the three-pool model it
calls "the balance-bucket trap") and `senpi-strategy-ops/references/lifecycle.md` (deploy preflight:
"never `total_withdrawable`"). Those two were right and discover was wrong; this keeps them aligned.

Run: python3 -m pytest senpi-strategy-discover/tests/test_accessible_usdc.py -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "senpi-strategy-discover", "scripts"))

import discover  # noqa: E402

FIXTURE = os.path.join(ROOT, "senpi-portfolio", "tests", "fixtures", "portfolio_fixture.json")


def _fixture_portfolio():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)["account_get_portfolio"]["portfolio"]


def test_committed_capital_is_never_counted_as_a_budget():
    """The regression in its own terms, on the repo's own fixture."""
    p = _fixture_portfolio()
    # what the buckets actually say
    assert p["total_balance_usd"] == 3102.94
    assert p["total_withdrawable"] == 2461.98
    assert p["total_allocated_in_strategy"] == 3101.43
    assert float(p["total_in_hyperliquid"]) == 0.0

    got = discover._accessible_usdc(p)
    assert got == 1.51, (
        f"accessible USDC read {got}, expected 1.51 (the lone EVM USDC balance). This account has "
        f"$3,101.43 live inside strategies; quoting total_balance_usd ($3,102.94) or "
        f"total_withdrawable ($2,461.98) offers the user money that is already working.")
    assert got < p["total_withdrawable"] and got < p["total_balance_usd"]


def test_a_zero_balance_is_a_fact_not_a_missing_read():
    """`total_balance_usd or total_in_hyperliquid` was an `or` chain, and 0 is falsy.

    That is the mechanism: a user with nothing free reads 0, the chain decides the field is absent,
    and reaches for the next one — which is the committed-capital figure. A real 0 must survive."""
    assert discover._accessible_usdc(
        {"total_in_hyperliquid": 0, "total_spot_usd_in_hyperliquid": 0,
         "total_withdrawable": 5000, "total_allocated_in_strategy": 9000,
         "total_balance_usd": 14000}) == 0.0


def test_the_three_deployable_buckets_add_up():
    assert discover._accessible_usdc({
        "total_in_hyperliquid": 100.0,
        "total_spot_usd_in_hyperliquid": 25.5,
        "token_balances": [
            {"tokenSymbol": "USDC", "balanceInUSD": 10.25},
            {"tokenSymbol": "WETH", "balanceInUSD": 999.0},   # not a stable — excluded
            {"tokenSymbol": "usdt", "balanceInUSD": 4.25},    # case-insensitive
        ],
        "total_withdrawable": 7777.0,                          # must not appear
    }) == 140.0


def test_unreadable_values_do_not_crash_or_inflate():
    """A string, a None or a malformed row contributes nothing — it must never fall through to a
    committed-capital field as a 'better' number."""
    assert discover._accessible_usdc({
        "total_in_hyperliquid": "not-a-number",
        "total_spot_usd_in_hyperliquid": None,
        "token_balances": [None, "junk", {"tokenSymbol": "USDC", "balanceInUSD": "x"}],
        "total_balance_usd": 50000.0,
    }) == 0.0


def test_fetch_user_context_uses_the_accessible_figure():
    """The helper is only worth having if the caller actually uses it — the previous bug lived in
    the caller, not in any helper."""
    class _Client:
        def mcp_call(self, name, **kw):
            assert name == "account_get_portfolio"
            return {"data": {"portfolio": _fixture_portfolio()}}

    ctx = discover.fetch_user_context(_Client())
    assert ctx["budget"] == 1.51, (
        f"fetch_user_context reported {ctx['budget']} as the budget. It must report only what can "
        f"be deployed, not total_balance_usd.")


def test_the_source_no_longer_reads_a_committed_capital_field_as_budget():
    """Guard the specific idiom, so the `or` chain cannot come back by a different route."""
    with open(os.path.join(ROOT, "senpi-strategy-discover", "scripts", "discover.py"),
              encoding="utf-8") as fh:
        src = fh.read()
    assert 'ctx["budget"] = _accessible_usdc(' in src, "budget no longer comes from the waterfall"
    for banned in ('ctx["budget"] = data.get("total_balance_usd")',
                   'data.get("total_balance_usd") or',
                   'ctx["budget"] = data.get("total_withdrawable")'):
        assert banned not in src, f"discover.py reads a committed-capital field again: {banned}"
