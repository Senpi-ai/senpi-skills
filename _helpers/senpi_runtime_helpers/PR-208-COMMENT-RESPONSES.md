# PR #208 — Review-comment dispositions

This file records review comments on
https://github.com/Senpi-ai/senpi-skills/pull/208 that were intentionally NOT
addressed in code, with the rationale for each. Comments that were addressed
in code are documented in the corresponding fix commits and are not listed
here.

---

## Cursor Bugbot — `pangolin/scripts/pangolin-producer.py:173`

> **Test wallet budget hardcoded disabling dynamic cap safety** (High Severity)
>
> `STARTING_BUDGET` was changed from `1000.0` to `160.0` with the comment
> "rebased for $160 test wallet." This constant is used by
> `get_dynamic_daily_cap()` to calculate PnL-based entry limits. With a $160
> baseline, any wallet with more than ~$168 value would compute >5% PnL and
> always receive the maximum 12 entries/day, effectively disabling the
> graduated safety mechanism that reduces entries on drawdowns.

**Disposition: keep $160.0 — intentional.**

The $160 value matches the live test wallet currently running this branch.
The dynamic cap is still useful **for that wallet** because drawdowns from
$160 still graduate the cap downward (e.g., wallet drops to $130 → -18.75% →
cap = 1). The "always 12 entries" path only kicks in once the wallet
appreciates above ~$168, which is the bracket the test deployment is
intentionally exercising.

The fleet baseline will revert to `1000.0` (or be made wallet-relative) when
the Pangolin producer migrates beyond the test deployment. That work happens
in a follow-up PR alongside the next round of fleet tuning; treating it as a
gate on this PR would block the wrapper migration on unrelated tuning work.

The bugbot comment is left **unresolved** in GitHub (rather than resolved) so
the issue remains visible until the post-test rebase lands.
