# Why hasn't it traded? — reason codes, no signals, no codes

Read before answering "why hasn't it traded / why didn't it open that position". The always-on rules
are in SKILL.md ("Liveness and why hasn't it traded"); this is the detail.

## "Why hasn't it traded?" / "why didn't it open that position?" — answer from the outcome codes

A running, healthy strategy that has not traded is the single most common "is it broken?" question,
and the answer is almost never "it's broken". Every evaluated signal carries a
`senpi.outcome.reason_code`, so **say which one, how many, and whether the user needs to do
anything** — never "I'm not sure why."

**Two rules before you answer.** Count the signals rather than describing one, and name the assets
that were skipped, because "3 eligible entries were skipped" is the part the user actually feels.
And lead with whether they need to act: for most of these the honest answer is *nothing*, and saying
so is the useful reply.

| reason code | what to tell the user | do they act? |
|---|---|---|
| `withdrawable_unavailable` | "Your available balance couldn't be read during **N** recent scans, so these eligible positions were skipped. Your funds aren't affected and it usually clears on its own within a few scans. If it's still happening after that, contact Senpi support (the chat on senpi.ai)." | only if it persists |
| `insufficient_margin` | "Your available balance is fully deployed, resulting in these eligible positions being skipped." It opens on its own as soon as a position closes. | no |
| `no_margin_configured` | The strategy has no way to size a position, so it can never open one. Redeploy it, or contact Senpi support (the chat on senpi.ai) if redeploying doesn't fix it. | **yes** |
| `no_slots` | Every slot is already holding a position — working as designed. | no |
| `below_min_notional` | The position would be smaller than the $10 exchange minimum. More budget, or higher leverage, would clear it. | their call |
| `risk_gate_COOLDOWN` | A guard rail is holding it back — the per-asset or global cooldown from its own config. | no |
| `risk_gate_CLOSED` | A guard rail tripped — daily loss limit, drawdown halt, or consecutive-loss brake. The gate carries its own `reason`, so name **which one**; never just "a risk gate". The daily loss and drawdown halts **latch**: they stay closed after PnL recovers. | no, until it resets — but a drawdown halt with `drawdown_reset_on_day_rollover: false` never resets on its own: that is **the user's decision**, never clear it for them |
| `position_already_exists` | It already holds that asset; it will not double up. | no |
| `strategy_backend_paused` | The strategy is paused — it will not open anything until it is resumed. | **yes** — unpause it |
| `position_open_failed` | It tried to open and the exchange rejected the order. Give the exchange's own message, which rides the outcome. | depends on the message |
| `timeout` | **Not a rejection — the order may have filled.** The runtime recorded a failure after the send timed out, so the user can be holding a position it does not know about. Check live positions before saying anything, and never suggest retrying until you have. | check positions first |
| `exception` | An unexpected error on the open path, with the message on the outcome. Contact Senpi support (the chat on senpi.ai) if it repeats. | if it repeats |
| `invalid_direction` | A defect — the signal carried neither LONG nor SHORT. Contact Senpi support (the chat on senpi.ai). | **yes** |

**Do not offer "close and relaunch" for `withdrawable_unavailable`.** It reads like the obvious fix
and it is not one: relaunching rebuilds the same percent-sized recipe against the same balance read,
and a fresh wallet does not mend a read that is wedged. Wait, then support.

Do not send anyone to redeploy over a row marked "no". A fully deployed balance and a missed balance
read both resolve on their own, and tearing down a working strategy over either is worse than saying
nothing.

## No signals at all — the scanner is alive but producing nothing

The table above only covers signals that were **evaluated**. A scanner producing none has no reason
code at all, so the codes tell you nothing.

Two commands, and they answer different questions — `senpi runtime list` prints only id, source and
status, so it will show you `running` and leave you stuck. It does carry the sibling failure,
`running — NO ENTRY SCANNERS`. The per-scanner view is **`senpi scanner`**, which flags a barren one
in plain words (`(no signals yet)` = it has run and emitted nothing). For anything deeper, hand off to
`senpi-strategy-ops` `status.py <id>` (step 1 of the ladder in `runtime-health.md`) — this skill interprets, it does not
re-derive.

**The interpretation is the part that is yours**, because both of these look identical on every field:

- **Nothing qualified.** The entry bar has not been met — name it, never "being selective": the binding
  gate, its threshold and the universe count from its `runtime.yaml` (`senpi-strategy-ops`
  `references/liveness-verification.md`, "Healthy, live — and it has never traded"). Then judge against the
  mandate: a slow, high-conviction design is *supposed* to sit; a gate no readable value can meet is broken.
- **The scanner is blind.** Its market-data connection broke and every read has failed since, while it
  kept reporting healthy. The tell is a scanner that **used to** produce signals and has produced none
  for hours with no errors.

**Never call it healthy on the strength of `health=healthy`.** For this failure that field is exactly
the thing that is wrong — which is why users find it by noticing the quiet, not from anything we show
them. When it is the second one, say so and give the action:

> "Your scanner has been running for the last 9 hours without producing a single candidate, and it was
> producing them before. That usually means its market-data connection dropped — the strategy isn't
> broken and your funds aren't affected, but it isn't looking at the market either. Restarting clears
> it. If it comes back, contact Senpi support (the chat on senpi.ai)."

## No reason codes at all — read it the right way round

"No codes" splits three ways, and routing it wrong sends the user to the wrong place:

- **No codes AND no signals** → the scanner, as above.
- **No codes but signals ARE present** → look for `clearinghouse_unknown`. The runtime refuses to open
  against an exchange view it could not read, so it skips the whole batch wholesale. It is the one
  outcome that never reaches the per-signal path, so it carries **no `reason_code`** and will not
  appear in the table. The scanner is working fine; the exchange read is not. Same family as the
  wedge above, and the same answer: it clears on its own or on a restart, and persisting means support.
- **No runtime registered at all** → `runtime-health.md` ("ACTIVE ≠ running"), not this one.
