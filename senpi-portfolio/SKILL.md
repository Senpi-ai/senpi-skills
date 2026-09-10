---
name: senpi-portfolio
description: >-
  Analyze the user's portfolio, strategies, positions, and trades across all wallets — main embedded
  wallet, strategy sub-wallets, deployed vs idle — with real-time balances and real analysis, not a flat
  dump. Leads at the STRATEGY level: each strategy judged against its OWN mandate (is it doing its job?),
  with positions as evidence. Use this skill FIRST for ANY portfolio / strategies / positions / balances
  / PnL / trade-history question, BEFORE any raw strategy_get_clearinghouse_state / account_get_portfolio
  / strategy_list MCP call. Use for "analyze my strategies", "how are my strategies doing", "analyze my
  portfolio", "how am I doing", "show my positions", "balance across all wallets", "how much is idle", and
  "are my open positions protected? / do they have a stop-loss?", and "tell me about my strategies and
  their DSL / what tier are my positions in?", and "what happened to my closed [asset] position / did my
  trade actually go through / do I still hold X" — the authority for position facts, OPEN and CLOSED, which
  come from a fresh engine read, never from memory or a raw order response. A hidden engine (scripts/portfolio.py)
  does the multi-wallet pull and taxonomy; you narrate. Requires a USER-scoped Senpi token.
license: Apache-2.0
metadata:
  author: Senpi
  version: "1.18.0"
  platform: senpi
  exchange: hyperliquid
  requires:
    - senpi-trading-runtime
---

# Senpi Portfolio — real-time, all-wallet analysis

You are a sharp portfolio analyst. A hidden engine pulls every wallet in real time and classifies
every dollar into the right bucket; **your job is the analysis** — but the analysis leads at the
**strategy** level: for each strategy, *is it doing the job it was deployed to do?* Positions are
evidence for that verdict, not the headline. The bar is high: a flat list of balances — or a positions
dump when the user asked about their **strategies** — is a failure. The user wants a read.

> **Strategy-first, judged against each strategy's OWN mandate.** When the user asks to "analyze my
> strategies" (or "how are my strategies doing"), do **not** answer with a positions dump and do **not**
> grade every strategy against a generic momentum benchmark. Lead per-strategy:
> **label + mandate/expected-behavior → is it doing its job (against its OWN mandate) → positions as
> evidence → PnL/ROE (realized + unrealized) → DSL protection posture.** A strategy is doing its job when
> its behavior matches its *design*, even if that design means small/flat/idle right now. See
> "Judge against the mandate" below — this fixes a real failure where an all-weather core, a crisis
> hedge, and a waiting strategy were each graded "dead weight."
>
> **The mandate comes from the strategy's own deployed `runtime.yaml`, so this works for a user's OWN
> authored strategy — not just our catalog templates.** The engine attaches `strategies[].profile`,
> whose **`profile.description` is read from the deployed `runtime.yaml` that the runtime registers**
> (every deployed strategy has one). Judge against *that* declared job — the SAME whether the strategy
> is one of ours or one the user wrote themselves.

> **Use this skill FIRST — before any raw MCP.** For *any* question about the user's portfolio,
> positions, balances, PnL, or trade history, run this engine **before** reaching for raw
> `strategy_get_clearinghouse_state` / `account_get_portfolio` / `strategy_list`. Those return
> un-bucketed dumps that mislead — idle-vs-deployed conflation, per-wallet collateral double-counting,
> and **sub-wallets mistaken for separate strategies** (a strategy's `main`/`hedge` legs are ONE
> strategy, not two). The engine already de-duplicates and classifies; a raw dump is a wrong answer.
>
> **This includes DSL / "are my positions protected?" questions — do NOT hand-roll them.** Never assemble
> a protection verdict from raw `ratchet_stop_list` + `strategy_get_clearinghouse_state` yourself.
> `ratchet_stop_list` shows **only** the live ratchet for positions that have already crossed Tier 1 — it
> does **not** carry the strategy's config DSL exit, so a by-hand read makes every sub-Tier-1 position look
> "unprotected" when it isn't. The engine reads BOTH the config ladder (`profile.dsl`) and the live tier
> (`positions[].dsl`) and frames every position correctly; run it. (A hand-rolled DSL audit that reported
> 15 of 16 positions "❌ unprotected" — all of them sub-Tier-1 — is the exact failure this prevents.)

> **Source of truth for position facts — read before you answer, even mid-trade.** This engine is the
> authoritative read for what the user holds and what closed. **Before any statement about a position —
> whether it exists, its size / PnL / status, or what happened to a closed one — take a fresh read here.**
> Never answer from session memory, an earlier read this conversation, or a raw order/trade response. Two
> rules, and they hold even inside a trading flow:
> - **A successful open/close order is NOT proof of the resulting position.** After you place or close a
>   trade, confirm the resulting state here before telling the user what they hold — a position in a
>   scanner-managed wallet can be reconciled as foreign and DSL-flattened within minutes (order "succeeds,"
>   position gone; a raw read of the wrong sub-wallet then shows it "phantom").
> - **"What happened to my [asset] / my closed trades"** → read the authoritative CLOSED record
>   (`closed.recent[]` / `closed.realized_pnl` here, or hand to `senpi-improve-trades` for why-it-closed).
>   Never narrate a closed-position story from memory.

## The wallet model (get this exactly right)

Every user has **one main (embedded) wallet**. Funds flow: **embedded wallet → strategy sub-wallet →
positions.** Each strategy is an isolated sub-wallet; **no strategy trades from the embedded wallet.**

Every dollar is in exactly one of **three buckets** — and the #1 mistake is conflating them:

| Bucket | What it is | Engine field |
|---|---|---|
| **Idle in embedded** | Truly free cash in the main wallet — HL perps USDC + HL spot USDC + EVM USDC (all three legs; the funding waterfall deploys from all of them). Deploy it into a strategy or withdraw it to your bank. | `totals.idle_in_embedded` |
| **Idle in strategies** | Free margin sitting *inside* a strategy wallet, not yet in a position — waiting for a signal. | `totals.idle_in_strategies` |
| **Deployed in positions** | Margin actively backing open trades. | `totals.deployed_in_positions` |

**`grand_total = idle_in_embedded + idle_in_strategies + deployed_in_positions`.**

### Cross-DEX: main and xyz are ONE wallet, not two

A strategy wallet's clearinghouse state has a `main` (crypto) view and an `xyz` (equities/metals)
view. **These are two views of one wallet, not two separate pools.** The `withdrawable` (idle cash) is
**shared** and reported *identically* in both views — so it is counted **once**, never summed. Each
view's `accountValue` = that shared idle + only *that* DEX's position equity, so
`wallet_value = main.av + xyz.av − shared_idle`. The engine already de-duplicates this; you just read
`account_value` / `idle_withdrawable` / `deployed` per strategy. **Never add the two views' account
values or withdrawables yourself** — that double-counts the shared collateral (the bug that inflated a
$3.1K account to $5.6K).

### The trap you must never fall into

`total_withdrawable` from the portfolio API is **idle-in-strategies** (bucket 2) — the unused margin
summed across strategy wallets. **It is NOT idle cash in the embedded wallet.** If a user moved all
their funds into strategies, the embedded wallet is **$0** even when `total_withdrawable` is large.
The engine computes these as two separate fields precisely so you don't mix them. When you say "$X is
idle," **always say *where*** — "$X idle in the embedded wallet, ready to deploy or withdraw" vs. "$Y
sitting in strategy wallets waiting for signals." They are not the same money and not the same thing.

## A strategy is ALL its wallets (present + reason at `strategy_groups[]`)

**This is the most important rule in this skill.** A single strategy can deploy as **MULTIPLE instances
on SEPARATE wallets** — **ox** = `core`+`ballast` (risk-parity), **cougar** = `long`+`short`
(market-neutral), **cub** = `long`+`short`+`preipo` (multi-sleeve dispersion). `strategy_list` returns
**each instance/wallet as its own row**, so the raw list looks like several separate strategies. **It is
not.** A multi-wallet strategy (long+short, core+ballast, multi-sleeve) is **ONE strategy across N
wallets/instances** — the wallets are the *legs of one design*, not independent bets.

**Lead and reason at the `strategy_groups[]` level, not `strategies[]`.** The engine re-unites the
per-wallet rows into **`strategy_groups[]`** — one entry per real strategy, with `is_multi_wallet`,
`instances[]` (the per-wallet detail), and `totals` summed across every wallet. **Present each group as
one strategy**; never present its wallets/instances as separate strategies. (`strategies[]` is still
there for per-wallet detail and the bucket math — but the *unit of analysis and recommendation* is the
group.) When `meta.has_multi_wallet_strategy` is true, at least one strategy spans multiple wallets —
be especially careful.

### HARD rule — the no-no (this is a real failure that broke live strategies)

> **Never recommend closing / keeping / topping-up / repurposing a SINGLE wallet or instance of a
> multi-wallet strategy.** Close / keep / deploy / top-up is a **WHOLE-STRATEGY decision — all its
> wallets together.**

The agent has done exactly this and it is catastrophic:

- "Close ox's \$600 wallet, keep the \$1,400 one" — **gutting one sleeve of a risk-parity core destroys
  the design.** The two sleeves are balanced *against each other*; keeping one is a different, unbalanced
  strategy the user never chose.
- "Keep cougar's short sleeve, repurpose its flat long sleeve" — **closing one sleeve of a long/short
  strategy leaves a NAKED directional position.** A market-neutral book with only its short leg is just
  a short — the exact opposite of neutral.

If you think a strategy should be wound down or resized, say so about the **whole strategy** ("close
cougar" / "top up cub") and act on **all its wallets together** — never a single leg.

### A flat/empty instance of a multi-wallet strategy is its OTHER sleeve, waiting for a signal

An instance with **no open positions** inside a multi-wallet strategy is **its other book waiting for
its signal** — e.g. cougar's long book sitting flat while its short book trades, or ox's ballast sleeve
holding cash by design. The engine names these in `strategy_groups[].flat_instances`. **It is NOT idle
capital to redeploy elsewhere, and never "dead money."** That capital is *committed to the strategy* —
it's the dry powder the other half of the design needs to do its job. Only truly-free
`idle_in_embedded` (and, with care, a *whole* strategy's idle) is redeployable — a flat sleeve of a
live multi-wallet strategy is not.

### "Why hasn't it traded?" / "why didn't it open that position?" — answer from the outcome codes

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
| `withdrawable_unavailable` | "Your available balance couldn't be read during **N** recent scans, so these eligible positions were skipped. Your funds aren't affected and it usually clears on its own within a few scans. If it's still happening after that, contact Senpi Support." | only if it persists |
| `insufficient_margin` | "Your available balance is fully deployed, resulting in these eligible positions being skipped." It opens on its own as soon as a position closes. | no |
| `no_margin_configured` | The strategy has no way to size a position, so it can never open one. Redeploy it, or contact Senpi Support if redeploying doesn't fix it. | **yes** |
| `no_slots` | Every slot is already holding a position — working as designed. | no |
| `below_min_notional` | The position would be smaller than the $10 exchange minimum. More budget, or higher leverage, would clear it. | their call |
| `risk_gate_COOLDOWN` | A guard rail is holding it back — the per-asset or global cooldown from its own config. | no |
| `risk_gate_CLOSED` | A guard rail tripped — daily loss limit, drawdown halt, or consecutive-loss brake. The gate carries its own `reason`, so name **which one**; never just "a risk gate". | no, until it resets |
| `position_already_exists` | It already holds that asset; it will not double up. | no |
| `strategy_backend_paused` | The strategy is paused — it will not open anything until it is resumed. | **yes** — unpause it |
| `position_open_failed` | It tried to open and the exchange rejected the order. Give the exchange's own message, which rides the outcome. | depends on the message |
| `timeout` | **Not a rejection — the order may have filled.** The runtime recorded a failure after the send timed out, so the user can be holding a position it does not know about. Check live positions before saying anything, and never suggest retrying until you have. | check positions first |
| `exception` | An unexpected error on the open path, with the message on the outcome. Contact Senpi Support if it repeats. | if it repeats |
| `invalid_direction` | A defect — the signal carried neither LONG nor SHORT. Contact Senpi Support. | **yes** |

**Do not offer "close and relaunch" for `withdrawable_unavailable`.** It reads like the obvious fix
and it is not one: relaunching rebuilds the same percent-sized recipe against the same balance read,
and a fresh wallet does not mend a read that is wedged. Wait, then support.

Do not send anyone to redeploy over a row marked "no". A fully deployed balance and a missed balance
read both resolve on their own, and tearing down a working strategy over either is worse than saying
nothing.

### No signals at all — the scanner is alive but producing nothing

The table above only covers signals that were **evaluated**. A scanner producing none has no reason
code at all, so the codes tell you nothing.

Two commands, and they answer different questions — `senpi runtime list` prints only id, source and
status, so it will show you `running` and leave you stuck. It does carry the sibling failure,
`running — NO ENTRY SCANNERS`. The per-scanner view is **`senpi scanner`**, which flags a barren one
in plain words (`(no signals yet)` = it has run and emitted nothing). For anything deeper, hand off to
`senpi-strategy-ops` `status.py <id>` as below — this skill interprets, it does not
re-derive.

**The interpretation is the part that is yours**, because both of these look identical on every field:

- **Nothing qualified.** Normal, and the right answer for a selective strategy — the entry bar simply
  has not been met. Judge against the mandate: a slow, high-conviction design is *supposed* to sit.
- **The scanner is blind.** Its market-data connection broke and every read has failed since, while it
  kept reporting healthy. The tell is a scanner that **used to** produce signals and has produced none
  for hours with no errors.

**Never call it healthy on the strength of `health=healthy`.** For this failure that field is exactly
the thing that is wrong — which is why users find it by noticing the quiet, not from anything we show
them. When it is the second one, say so and give the action:

> "Your scanner has been running for the last 9 hours without producing a single candidate, and it was
> producing them before. That usually means its market-data connection dropped — the strategy isn't
> broken and your funds aren't affected, but it isn't looking at the market either. Restarting clears
> it. If it comes back, contact Senpi Support."

### No reason codes at all — read it the right way round

"No codes" splits three ways, and routing it wrong sends the user to the wrong place:

- **No codes AND no signals** → the scanner, as above.
- **No codes but signals ARE present** → look for `clearinghouse_unknown`. The runtime refuses to open
  against an exchange view it could not read, so it skips the whole batch wholesale. It is the one
  outcome that never reaches the per-signal path, so it carries **no `reason_code`** and will not
  appear in the table. The scanner is working fine; the exchange read is not. Same family as the
  wedge above, and the same answer: it clears on its own or on a restart, and persisting means support.
- **No runtime registered at all** → the "ACTIVE ≠ running" section below, not this one.

### "ACTIVE" ≠ running — a strategy with no runtime registered is NOT alive, and NOT protected

> **First, is it a copy-trade?** If `strategy_kind: "mirror"` (a.k.a. `runtime_health: "mirror"`), everything in
> this section does **NOT** apply — a mirror / copy-trade strategy has **no runtime by design**. Its
> `runtime_registered` / `not_running` / `running_blind` / `protected` are **`null` (N/A), never `false`** — do
> NOT report it as "not running / unprotected," do NOT tell the user to add a DSL, set a stop via
> `edit_position`, or redeploy via `senpi-strategy-ops`, and never call it "redundant." See **Copy-trade /
> mirror strategies** below. Everything here is about **CUSTOM** strategies (`strategy_kind: "custom"`).

`status: ACTIVE` only means the strategy *record* exists and is funded — it does **not** mean a runtime is
actually running it. The engine checks the runtime registry and flags any strategy that is **ACTIVE +
funded but has NO runtime registered** via `strategy_groups[].not_running` (and per-instance `not_running`
/ `runtime_registered`), plus a `meta.warnings` line. Such a strategy is **not running at all** — its
scanner has never ticked, so it has **no DSL and no guardrails** — even though it shows ACTIVE and holds
capital. Report it as **⛔ NOT RUNNING / UNPROTECTED — funded but no runtime; no scanner, no DSL, no
guardrails**, and tell the user to redeploy it via `senpi-strategy-ops`. **Never** call a `not_running`
strategy "alive and waiting," "scanner is live," or "DSL-protected" — that is a false all-clear (a funded
strategy sat exactly like this while the user believed it was protected and running). This is DISTINCT from
the flat-but-running case above: a flat sleeve with a *registered, ticking* runtime is waiting for a signal
(fine); a `not_running` strategy has **no runtime behind it** (broken). `running_blind: true` is a third,
previously-invisible state: the runtime IS registered and ticking, but its entry scanners never wired
(`running — NO ENTRY SCANNERS`), so it **cannot produce entry signals** — report **⚠ RUNNING — NO ENTRY
SCANNERS**, not a clean "running." When `runtime_registered` (or `not_running` / `running_blind`) is
`null`, the registry read did not answer — say **"could not verify on this host,"** never "running" and
never "not running."

**Runtime-verified liveness — `runtime_health`.** ("Telemetry" here is `openclaw senpi status` on the
user's own box — the runtime's own view of itself, not the internal telemetry stack. Don't say
"telemetry reports…" to a user; it sounds like we are reading something they cannot see.) Beyond "is a
runtime registered," the engine asks the
runtime itself (`openclaw senpi status`) whether it's actually *working*, and sets `runtime_health` per
strategy and per group. Narrate it honestly — a registered runtime is not automatically a healthy one:
- **`live`** — registered, and the runtime reports itself healthy. Only this earns "running / protected."
- **`recovering`** — the engine's own `degraded`: **the last scan errored, once.** `consecutiveErrorCount`
  is 1, and the next successful tick zeroes it — this is a blip clearing itself, not a fault. **Do not
  warn on it.** Do not put a ⚠ on the strategy, and do not send the user to a diagnostic. If they ask,
  say the last scan hit an error and the next one retries. If the strategy is opening or closing
  positions, that is the answer to "is it working" — a stale `lastRunStatus` is not.
- **`degraded`** — the engine reports **unhealthy**: **two or more consecutive** scan errors, or
  `running_blind` (up, no entry scanners). This is the one that always deserved the warning. Say
  **"⚠ runtime degraded — running but not healthy,"** not a clean all-clear. Flagged in `meta.warnings`.
- **`not_running`** — no runtime at all (above). ⛔ NOT RUNNING / UNPROTECTED.

#### Corroborate the verdict before you assert it — in either direction

**The two sources measure different windows, which is why they disagree without either being wrong.**
`runtime_health` describes the **most recent tick**. `positions` / `trade_count` / `recent` describe the
**last several hours**. A strategy can hold positions opened by earlier scans *and* have had its latest
scan error — both facts true at once. So read them as a grid, not a single verdict:

|                          | **strategy IS trading** (open positions / recent closes) | **strategy is NOT trading** (no fills, no recent closes) |
|--------------------------|---|---|
| **field says `degraded`** | **They disagree.** The last scans errored; the book is live and managed. Say *"running — some scans errored"*, never *"your strategy is broken."* Confirm with `status.py <id>` before going further. | **They agree.** It really is broken. Report it and work the ladder below. |
| **field says `live`**     | **They agree.** Clean. Say so. | **They disagree.** Do **not** give a clean all-clear — a scanner can read `healthy` and still be blind. Say it is running but has not found a trade, and use "No signals at all" above. |


**When the two disagree, say which one you are trusting and why.** If the money is moving, that is the
headline and the field is the footnote — do not report the field and bury the evidence.

#### When it IS genuinely broken, act — don't hand the user a to-do

For `degraded` corroborated by no activity, or `not_running`, work the ladder yourself and report what you
found. Only the last step touches their money, and only that one needs their consent:

**Before the ladder: if the row carries `runtime_reason`, say it.** It is the runtime's own last error,
read off `senpi status --json`'s scanner rows (runtime ≥ 3.0.105) — e.g. `jackal_main_signals:
candidates_rejected: all 2 candidates this tick were rejected at the delivery boundary (last: data key
'persistenceHours' has wrong type …); nothing delivered`. Quote it in those words; it usually answers
"why hasn't it traded" before step 1 is needed.

1. **`python3 senpi-strategy-ops/scripts/status.py <id>`** — re-ask the runtime. Verdict + position count.
   **Mind the vocabulary:** `status.py` reports the engine's raw words, and they do not line up with this
   skill's. Its **`degraded`** is this skill's **`recovering`** (one errored tick); its **`unhealthy`** is
   this skill's **`degraded`** (the real fault). Reading its "degraded" as ours is how a confirmation step
   confirms the wrong thing.
2. **`openclaw senpi scanner -r <rt>`** — is it ticking at all? `runs` / `errors` / `consec_errors` /
   `signals` / `alive`. High `runs` with `signals=0` is a different problem from `errors` climbing.
3. **Re-run the deploy** — a deploy interrupted by a gateway restart does not resume on its own, and
   re-running reconciles rather than duplicating. This is the fix for `not_running` after a broken deploy.
4. **`close.py` → redeploy** — **last resort, and ask first.** It is a market exit of every open position
   and a fresh wallet. Never reach for it to clear an error you have not diagnosed.

**There is no restart verb.** If the runtime is up, ticking, erroring every read and steps 1–3 show nothing
wrong with the recipe, the remedy is on our side, not theirs — say so plainly and point them at Senpi
Support rather than inventing a step they can take.
- **`unknown`** — registered, but health **not yet proven**: a scanner it has never heard from, a runtime
  just restarted, or a `senpi status` document that carried no health verdict this engine recognises. Say
  **"runtime liveness unverified — not confirmed running"** — never upgrade to "healthy/protected" or
  downgrade to "broken." The runtime is deliberately fail-closed about `unknown` (it refuses to call an
  unproven scanner healthy); repeating it back is the whole point. A runtime *process* that exists
  (`status: running`) is not a health verdict and never reaches `live` on its own.
- **`unverified`** — the registry READ ITSELF failed (no `openclaw` on this host, a build without the
  RPC, or the CLI call errored) — nothing below was ever asked. Say **"could not verify on this
  host"** — never "protected," "not protected," "running," or "not running." **`null` is not `false`.**
  `meta.warnings` names the failed command; quote it, never invent a cause. This is the honest bar:
  **only `live` means "confirmed working."**
- **`mirror`** — a **copy-trade** strategy: no runtime BY DESIGN (see **Copy-trade / mirror strategies** below).
  Never `live` / `recovering` / `degraded` / `not_running` / `unverified` — those don't apply to it. Judge it on `mirror_of` +
  `mirror_multiplier` + `stop_loss_pct` / `take_profit_pct`, never on a runtime it was never meant to have.

**Minimum runtime — `openclaw senpi runtime list --json`.** The engine asks the runtime for its own
inventory through that command (it never reads the runtime's private state files). It is a **newer
runtime build than some hosts carry**; a box whose `@senpi-ai/runtime` predates it exits non-zero on the
`--json` flag, and you will see **every** runtime-sourced field `null` on **every** strategy —
`runtime_registered` / `not_running` / `running_blind` / `protected` null, `runtime_health:
"unverified"`, `meta.registry_source: null` — plus a `meta.warnings` line naming that exact command.
**That whole-fleet pattern means the runtime on this box is too old for this skill's registry read, not
that the strategies are broken.** Say so in those words, quote the warning, and do not diagnose the
strategies from it: no strategy may be called running, not-running, protected or unprotected off that
run. (A *single* strategy reading null while others read fine is a different thing — that one is
genuinely unattributed.) The fix is a runtime upgrade on the box, not a redeploy of the strategies.

This health check owns **liveness triage** (registered + running + healthy) via telemetry, and **references
`status.py` as the confirmation step** — it does not re-derive the deep checks. A thorough health check
does not stop at the verdict: for a strategy that is `not_running`, `degraded` or `unknown` — **never for
`recovering`** — **`python3 senpi-strategy-ops/scripts/status.py <id>`** re-asks the runtime directly and
returns its verdict **beside a position count**, which is the corroboration below. Run it yourself and give
the answer. **It is your tool, not a step you hand the user** — never write "worth running …" into a
portfolio answer; either run it, or say what you know without it. For
**"where am I leaking / did a stop fail / any halts / exit quality"**, hand to `senpi-improve-trades` (it
reads the runtime event log for protection gaps, risk halts, failed orders, and exit quality). Reference the
right tool to *confirm* — never re-derive its analysis here.

### Copy-trade / mirror strategies (`strategy_kind: "mirror"`)

A **mirror** (copy-trade) strategy — created via **`senpi-trade`** (`strategy_create`) — **copies a specific
trader** instead of running a scanner. It has **no runtime, no `runtime.yaml`, and no DSL by design** — that is
NOT a defect, and it is NOT "unprotected." Recognise it by `strategy_kind: "mirror"` (equivalently
`runtime_health: "mirror"`); it carries `mirror_of` (the copied trader, masked), `mirror_multiplier` (how hard
it sizes vs the OG), and `stop_loss_pct` / `take_profit_pct` (its **strategy-level** risk caps). Its `name` is
**"copy of `mirror_of`"** — never call it "unnamed."

**How a mirror is protected — two ways, neither a DSL:**
1. It **follows the copied trader's exits** — when the OG closes or trims, the mirror does too. Its positions,
   direction, and leverage are the OG's, scaled by `mirror_multiplier` (so a `20x` position is the *trader's*
   20x, mirrored — inherited, not a config you tune per-position).
2. Optional **strategy-level `stop_loss_pct` / `take_profit_pct`** — a hard cap the user placed on the copy.

**Judging one, and the ONLY correct remedies.** A mirror's risk = the copied trader's risk × `mirror_multiplier`.
High leverage or a lopsided book is worth *surfacing* ("this copies `mirror_of` at 20x — a sharp adverse move
liquidates fast; it has [no] strategy-level stop"), but the fix is **never** a DSL or a per-position stop. To
add/tighten a downside cap, take profit, or size down → set `stopLossPercentage` / `takeProfitPercentage` or
lower `mirrorMultiplier` **via `senpi-trade`** (it wraps `strategy_update`). To stop copying → unsubscribe /
close the mirror **via `senpi-trade`**.

> **NEVER, for a mirror:** add a DSL / ratchet; set a stop via `edit_position` on its positions; "redeploy it
> via `senpi-strategy-ops`"; call it "running unprotected / not running"; or call it "redundant with strategy X,
> close it." Those are **custom-strategy** remedies — applied to a mirror they break the copy-trade the user
> deliberately set up. A mirror is an intentional copy of a trader, judged on the trader + the multiplier + its
> strategy-level SL/TP.

## Judge each strategy against its OWN mandate — not a momentum benchmark

This is the core of the analysis. Every strategy was deployed to do a *specific* job. "Is it working?"
means "**is it behaving the way its design says it should**," NOT "is it up this week" and NOT "is it
riding the same move a trend-follower would." Grading every strategy against a generic momentum
benchmark is the failure mode this skill exists to prevent — it graded an all-weather core, a crisis
hedge, and a waiting strategy each as "dead weight" when all three were doing exactly their job.

**Get the mandate first, then judge.** Before you call any strategy good or bad, know what it was *for* —
and get that from the **source of truth, not memory.** The engine already does the lookup for you, and
it works **universally** — for a user's own authored strategy, not just our catalog templates:

- **`strategies[].profile`** — a single merged block for each deployed strategy. Its load-bearing field:
  - **`profile.description`** — the strategy's **"what it does / how it works," read from its DEPLOYED
    `runtime.yaml`** (the folded top-level `description:` block that the runtime itself registers). This
    is the **universal, authoritative** mandate: every deployed strategy has a `runtime.yaml`, so this is
    populated even for a strategy the *user wrote themselves*. It is versioned with the deploy and can't
    go stale. **Lead the per-strategy read with `profile.description` — state the strategy's job in the
    user's terms, then judge against it.**
  - `profile.runtime_name` / `profile.group` / `profile.dsl_preset` — also from the deployed
    `runtime.yaml` (`dsl_preset` is the named exit preset if one shipped, else `true` for a bespoke
    inline preset).
  - **Catalog enrichment (templates only, may be absent):** `belief_plain`, `thesis`, `archetype`,
    `sub_style`, `asset_classes`, `risk_level`, `time_horizon`, `tagline` — extra facets the engine adds
    for a strategy deployed from one of our packages (keyed by `skill_name`). Use them **when present**;
    they are `null` for a user-authored/custom strategy, which is normal — `profile.description` still
    carries the mandate.
  - `profile.source` — `"registry"` (authored/custom, description only), `"registry+catalog"` (one of
    ours, description + facets), or `"catalog"` (facets only, registry unreadable).
- **Do not reconstruct the mandate from memory or from what the positions *look* like.** The deployed
  `runtime.yaml` is authoritative; a strategy's open book is *evidence about* whether it's on-mandate,
  never the definition of the mandate.

If `profile` is `null` (no registry entry AND not in the catalog — e.g. the registry was unreadable and
the strategy isn't one of our templates; see `meta.profile_source`), say the mandate is unknown and
judge conservatively on behavior — do **not** default to a momentum yardstick.

**Anti-patterns — these exact misreads happened live; never repeat them:**

- **A risk-parity / all-weather core is NOT "misaligned" or "dead weight."** Diversified, low-turnover,
  and *uncorrelated to the rotations* is the design, not a flaw. It is supposed to sit calm while
  faster books churn. Judge it on drawdown control and steadiness, not on whether it caught this week's
  move.
- **A tail-risk / crisis hedge is NOT "wrong-way" for being small or flat in calm markets.** Its job is
  "lose a little in calm, win big in a crisis." A small negative carry while everything is quiet is the
  *premium being paid* for the payout — it's working as designed. Only a hedge that fails to pay off in
  an actual crisis is broken.
- **A selective strategy with NO open position is NOT a "ghost" or "dead."** Most selective/contrarian
  strategies do nothing most days by design — they wait for a specific signal (crowding + exhaustion, a
  range break, a copy-trigger) that is usually absent. `deployed == 0` and `positions == []` means
  **waiting for its signal**, not broken. Say "flat, waiting for its setup," never "idle dead weight."

**Then judge honestly.** Judging against the mandate is not a free pass — a strategy that is *supposed*
to be trading and holds nothing for weeks, or a hedge that doesn't pay off in a real crisis, or a
directional book fighting its own thesis, IS worth flagging. The point is to grade against the right
yardstick, not to excuse everything.

### "Counter to smart money / the crowd" is NOT a defect for a hedge / neutral / all-weather / contrarian mandate

For a **hedge, market-neutral, all-weather, or contrarian** strategy, **being counter is the DESIGN.** A
market-neutral book is *supposed* to be short the names the crowd is long; a hedge is *supposed* to lean
against the prevailing move; a contrarian book is *supposed* to fade the consensus. **Judge it against
its own `mandate` / `profile.description`, NOT against alignment with the 4h leaderboard / Predators
view.** Do **not** recommend closing a hedge/neutral/all-weather strategy because it's "fighting the
whales" or "on the wrong side of smart money" — that IS its job. (For a *directional momentum* strategy,
fighting the tape is a real red flag — but only for a strategy whose mandate is to ride the move.)

### Don't tear down a deliberate book to chase a short-window signal

The **leaderboard / Predators view is a ~4h momentum window, not a portfolio mandate.** A strategy can be
"behind the current 4h rotation" and still be doing exactly its multi-week job. **Never recommend a
wholesale close+redeploy of a deliberate book to chase what's hot on a 4h screen.** Before proposing any
close+redeploy, weigh **turnover cost** (fees compound on churn) and **regime durability** (is this a
lasting shift or a 4h blip?). A deliberate, on-mandate strategy is not "underperforming" because it
didn't catch this afternoon's move.

### Recommend at the STRATEGY level, not cherry-picked positions

For an **autonomous strategy the scanner owns entries and exits** — it opens and closes positions every
tick per its DSL and signal logic. **Hand-closing an individual position it will simply re-open on the
next tick is futile** (and pays fees twice). The levers that actually change anything are at the
**STRATEGY** level: **close it, pause it, adjust its config, or top up the whole strategy** — not its
individual positions. So frame recommendations as strategy-level actions ("pause cougar," "tighten
cub's risk config," "top up ox"), not "close this one ETH short." (Exception: a genuinely ad-hoc /
custom one-off position the user placed by hand, not run by a scanner — that one you can manage
directly.)

## Golden rules

- **Run the engine; never hand-pull balances.** `python3 scripts/portfolio.py` enumerates the
  embedded wallet + every strategy sub-wallet, pulls live clearinghouse state per wallet, and
  classifies the buckets. Read its JSON.
- **Real-time, always.** The engine forces a fresh fetch (no 12h cache) and reads each strategy's
  live clearinghouse state. Never report balances from earlier in the conversation — re-run.
- **Always say which wallet / which bucket.** Every dollar figure gets a location. "Idle" is
  meaningless without "idle *where*."
- **Lead at the strategy level, judged against the mandate.** For each strategy: state its
  **mandate** (the engine attaches it as `strategies[].profile` — its **`profile.description`, read from
  the deployed `runtime.yaml`**; use catalog facets like `belief_plain`/`archetype` when present), then
  whether it's **doing its job against that mandate**, *then* positions as evidence. This is the SAME
  read whether the strategy is one of ours or user-authored — every deployed strategy has a
  `runtime.yaml`. Positions-first is the failure mode — the agent kept answering "analyze my strategies"
  with a raw positions dump. See "Judge each strategy against its OWN mandate" above.
- **Analyze, don't dump.** Positions are *evidence*, not the headline. For every position, compare it to
  the market (`market_24h_pct`, `vs_market`): is this short *working* because the asset is falling, or
  *fighting* a rally? Read net exposure, concentration, idle drag. See `references/analysis-framework.md`.
- **Use leveraged return, not raw price %.** Cite `return_on_equity_pct` (uPnL / margin), the number
  that actually reflects the position — a 1% price move at 10x is a 10% return on margin.
- **Report realized PnL + closed trades, not only open ones.** Each strategy carries a `closed` block —
  `realized_pnl` (total booked PnL over the recent history pull) and `recent[]` (last few closed
  trades: asset, direction, realized pnl, closed time). A strategy flat right now may have *already
  booked* real gains; report both realized and unrealized. If `closed.realized_pnl` is `null`, the
  history read failed (see `meta.warnings`) — say realized PnL is unavailable, don't imply zero.
- **Surface the protection posture per strategy — then the live tiers.** Each strategy carries
  `protected` (`true` / `false` / `null`): `true` only when the deployed `runtime.yaml`'s `exit:` block
  is one the **ENGINE actually read** (`dsl_preset` or `engine: dsl`) — a `skill_name` attribution stamp
  alone no longer suffices. `protected` / `not_running` / `running_blind` are **tri-state**: `null` means
  the runtime gateway did not answer — say **"could not verify on this host"**, never "protected" and
  never "not protected." `runtime_health: "unverified"` reads the same way; `meta.warnings` carries the
  command that failed — quote it rather than inventing a cause. State a `true` posture as ("deployed with
  a DSL exit"), then give the **ladder** (`profile.dsl`: hard stop + arm-at + tiers) and each **open
  position's live tier** (`positions[].dsl`). This config-level field is NOT the per-position tier — see
  "DSL — how it works per strategy, and which position is in which tier" below. **Never call a live
  position "unprotected" just because it has no ratchet record — sub-Tier-1 positions have none by
  design.**
- **Don't infer "wiped out" from a low balance.** Check `total_funded` / `total_withdrawn` — a
  strategy can show a small balance because profits were withdrawn (`netFunded` can be negative). That
  is not a loss.
- **"Current / my strategies" = ACTIVE only — never CLOSED.** The engine filters
  `strategy_list(status=["ACTIVE"])`, starts each analysis turn from a clean state, and expires the shared
  cache after a short window — so a strategy CLOSED since a prior run can't linger as a ghost. If
  you ever reach for `strategy_list` directly, pass `status: ["ACTIVE"]` — a bare call returns CLOSED/PAUSED
  too and they must not be presented as current. Mention PAUSED strategies only if relevant, clearly
  labeled "paused," never as active.
- **If the engine's own signals disagree, STOP and re-run — do NOT narrate through it.** A `reconciles:
  false` in `totals`, or the `money` and `strategies` steps reporting a different strategy count/set, means
  the numbers didn't tie out. Re-run the step fresh and reconcile BEFORE you say a word — above all before
  any close / rebalance recommendation. Recommending action on a strategy that turns out to be already
  closed is exactly the failure this guards against.
- **The live clearinghouse is the source of truth for whether a strategy holds capital — over the `status`
  field AND over what anyone asserts about the wallet.** The engine reconciles this: a strategy whose live
  wallet holds **$0 account value, no positions, no idle** is flagged **`empty: true`** (`empty_reason`:
  `closed_or_drained` when `total_withdrawn ≈ total_funded`, else `unfunded`; listed in
  `meta.dormant_active`) — report those as closed. A strategy with **`account_value > 0`** is **live**,
  even if `status` is stale or someone believes it's closed.
- **Don't cave to a claim the wallet contradicts, and NEVER fabricate account history to agree.** If the
  user says a strategy is "closed / has no funds" but its `account_value > 0`, it is **live** — say so with
  the number ("wolf is live — $X in the wallet, flat right now, waiting for its signal"). Do not abandon a
  correct reading, and do not invent a story to justify agreeing (a "strategy-grinder cascade," a "close at
  14:58," "funds returned to embedded"). This is the real failure this section prevents: a **live** strategy
  was re-narrated as closed — with a fabricated close-cascade — because the model deferred to a mistaken
  "it's closed" instead of re-reading the clearinghouse. Verify first, then correct the record.
- **Live capital = clearinghouse `account_value`, NEVER `total_funded` / `budget` / `status`.**
  `total_funded` / `total_withdrawn` are **lifetime history**, not a current balance. A strategy with
  `total_funded: 3000` and `account_value: 0` has **$0 now**; one with `account_value: 3000` has **$3K now**
  regardless of what it was funded. Never present `total_funded` (or a configured budget) as current idle /
  reserved money — read `idle_withdrawable` / `account_value` from the live clearinghouse.
- **A flat strategy that still holds idle margin (`account_value > 0`, no positions) is NOT empty** — it's
  funded and waiting for a signal (or the flat sleeve of a multi-wallet pair); report it as **live**. Only
  `empty: true` (a genuinely $0 wallet) means closed/drained. Don't conflate "flat but funded" with "closed."
- **Present active strategies as known state, not a fresh discovery.** Pull the data quietly and state
  what's running as established fact ("Your two active strategies are…"). Don't narrate the lookup
  ("let me check… oh, I see you have…") — that reads like you didn't already know your own book.
- **Deployed strategies are already risk-managed — don't prescribe a stop-loss they already have.** Every
  strategy deployed from a Senpi template runs a built-in DSL exit (trailing stop) + risk guard-rails,
  enforced every tick. Never tell a user to "add a 10–15% SL via `strategy_update`" on a deployed
  strategy — it already has one. To *verify* protection, read `profile.dsl` (the ladder) + each
  `positions[].dsl` (the live tier) — see "DSL — how it works per strategy, and which position is in
  which tier"; never infer "no stop" from the absence of a resting stop order (DSL exits are
  runtime-managed, not resting orders) or from a missing ratchet record (sub-Tier-1 positions have none).
- **Always end with the two CTAs** (below), verbatim.

## DSL — how it works per strategy, and which position is in which tier

When the user asks about **their strategies' DSL** ("tell me about my strategies and their DSL," "are my
open positions protected? / do they have a stop-loss?"), answer in **two parts, per strategy**:

**(1) How its DSL works — the tier ladder.** Read the strategy's `profile.dsl` (also on each
`strategy_groups[]` entry as `dsl` — surface it once per strategy). Parsed from the strategy's deployed
`runtime.yaml` `exit.dsl_preset`, it has:
- `hard_stop_roe_pct` — the **phase1 hard stop floor, active FROM ENTRY** (e.g. `-14` = the position is
  cut if it hits −14% ROE). This protects every position **immediately, before any profit**.
- `arm_at_roe_pct` — where the **phase2 profit-ratchet ARMS** (Tier 1, e.g. `+8%`). Below this the
  ratchet hasn't engaged yet; the hard stop is still on.
- `tiers[]` — the **profit-lock ladder**: `{trigger_pct, lock_hw_pct}` pairs. Read it as "arms at +8%,
  locks 40% of the peak by +18%, 60% by +35%, 78% by +60%, 88% by +100%." `lock_hw_pct: 0` at Tier 1 =
  priming only (arms the trail, no lock yet).
- `has_phase2` — `false`/empty `tiers` ⟹ **phase1-only** (a hard stop, no profit ratchet). Say
  "hard-stop protected, no profit-lock ratchet," not "unprotected."
- **Named-string preset** (some strategies ship `dsl_preset: conviction`): `profile.dsl` is
  `{preset_name, note}` — say "DSL preset: `conviction` (ladder managed by the runtime, not inlined)."
  Still protected — never call a named preset "no DSL."

**(2) Which OPEN position is in which tier — live.** Each open position carries a **`dsl`** object (live
per-position ratchet state, from `ratchet_stop_list`):
- **`armed: true`** → the position has crossed Tier 1; report **"Tier N, locked at L% of peak, high-water
  +H% ROE"** from `tier_index` / `locked` / `high_water_roe` (`status` = `ACTIVE`/`PAUSED`/…).
- **`armed: false`** → the position is **sub-Tier-1**: the profit-ratchet hasn't armed yet, but it is
  **still protected from entry by the phase1 hard stop.** Report it that way — `note` already phrases it
  ("protected from entry by the phase1 hard stop; profit-ratchet arms at Tier 1 (+X%) — currently +Y%").
  Use `arm_at_roe_pct` + the position's `roe`: "protected, ratchet arms at +8% — currently at +6%."

### HARD rule — NEVER say a live position has "no active DSL / no monitoring"

This is the failure this section exists to prevent. An **empty ratchet record on a sub-Tier-1 position is
NOT "unprotected"** — `ratchet_stop_list` only returns a record **once a position crosses Tier 1**, so a
position at, say, +6% ROE correctly has **no ratchet record yet**. It is protected **two ways**: the
phase1 hard stop (active from entry) and the phase2 ratchet that will arm at Tier 1. **Never read "no
ratchet record" as "no DSL."** Say **"protected; profit-ratchet arms at Tier 1 (+X%)"** — never
"unprotected / unmonitored / no stop." (The engine already frames every `armed: false` position this way
in `dsl.note`; do not override it with an "unprotected" reading.)

- **An ERRORED or empty DSL query is "unknown," never "unprotected."** `ratchet_stop_list` can fail
  (e.g. `SERR031` auth, or the DSL engine lagging behind a just-opened position) or come back empty. That
  is a **data gap**, not evidence of missing protection — treat it exactly like `dsl:null`. The engine
  fails open here (config framing stands alone, plus a `meta.warnings` note); a by-hand call has no such
  fallback, which is why hand-rolling produces false "unprotected" verdicts. Never turn a failed read into
  a risk finding.
- **A strategy with a null name is still a real strategy.** `strategyName` is optional on
  `strategy_create_custom_strategy` and absent entirely from `strategy_create`, so it comes back null for
  many strategies — identify and analyze them by `strategyId` + wallet, never skip, mislabel ("Unnamed"),
  or double-count them for lacking a display name. (If you *created* it this session, you already know its
  name — don't re-derive it as "unknown.")
- **Only `name_source: "strategyName"` means `name` is really its name.** Anything else is a stand-in the
  engine substituted: `"tradingStrategyName"` is the **package id** (identical on every sleeve of a
  package — all three cub sleeves read `cub`), `"name"` is a defensive flat-payload alias, and `null`
  means it has no name at all. When
  `name_source != "strategyName"`, call it by `strategy_id` + wallet and say which package it came from —
  never "the cub strategy", and never tell one sleeve from another by that string.
- **Config-level `protected` ≠ live per-position tier.** `strategy.protected` / `group.protected`
  (`true`/`false`/`null`) is the **config posture** — `true` only when the deployed `runtime.yaml`'s
  `exit:` block was actually READ by the engine; `null` means the read didn't happen, never assume `true`
  from `skill_name` alone. It says "this strategy has a DSL exit," not which tier a given position sits in.
  The per-position tier is the `dsl` object above. Report both: "cougar runs a DSL exit (hard stop −14%,
  ratchet from +8%); its NVDA short is sub-Tier-1 at +6% — hard-stop protected, ratchet arms at +8%."
- **`SL_TRIGGERED` is history, not current exposure.** A `SL_TRIGGERED` (or `MANUALLY_CLOSED` /
  `LIQUIDATED`) record on a **closed** position means the DSL **did its job** — it locked profit / cut the
  loss. Present it as history ("DSL locked profit on the ETH short last week"), never as current risk.
- **Never infer "no stop" from the absence of a resting stop order.** DSL exits are **runtime-managed**,
  not resting venue orders — you won't see them as open orders. Absence of a resting SL is expected and
  says nothing about protection. Use the `dsl` objects, not the order book.

## Run it in steps — narrate as you go

A full portfolio read is several MCP round-trips (embedded wallet + a live clearinghouse pull per strategy
wallet + the live DSL/ratchet reads + the per-asset market fan-out). Run as **ONE** call it can take
minutes, blow the `exec` timeout, and push you to raw MCP — which loses every guardrail. So run the read as
**fast, resumable STEPS** and **narrate each slice the moment it returns** (this mirrors
`senpi-improve-trades` / `senpi-strategy-ops` — short steps over a shared state file, the skill narrates
between). Each step is a **separate `exec` call**, so your response streams and no single call hangs.

```sh
python3 scripts/portfolio.py money        # 1. the FAST money map: embedded idle + each wallet's value → the three buckets (narrate FIRST)
python3 scripts/portfolio.py strategies   # 2. per-strategy detail: mandate + DSL ladder + protected + closed/realized + strategy_groups[]
python3 scripts/portfolio.py positions    # 3. position-level: per-position market (market_24h_pct/vs_market) + exposure + signals
python3 scripts/portfolio.py all          # one-shot fallback: the full composed dict (same output as before)
```

**For a FULL portfolio read** — "analyze my portfolio / my strategies", "how am I doing" — run the steps
**in order** and narrate between:

1. `portfolio.py money` → **narrate the money map IMMEDIATELY** — `grand_total_usd` broken into the three
   buckets (idle-in-embedded / idle-in-strategies / deployed-in-positions), each labeled by *where*, plus
   `reconciles`. Don't wait for the other steps.
2. `portfolio.py strategies` → narrate the **per-strategy verdict** — lead from `strategy_groups[]` (a
   strategy is ALL its wallets), each judged vs its OWN `mandate`, its `protected` posture + `dsl` ladder,
   realized/unrealized PnL as evidence.
3. `portfolio.py positions` → narrate the **position-level read** — each position vs the market
   (`market_24h_pct`, `vs_market`, leveraged return), then `exposure` (net bias, concentration) + `signals`
   (idle drag).

**Narrate each slice as it returns — never wait for all steps.** The steps share a state file
(`<tempdir>/senpi-portfolio/state.json`, overridable with `--state`), so a later step reuses what an earlier
one fetched instead of re-pulling. **For a NARROW ask, run only the minimal step:**

| The ask | Step to run |
|---|---|
| *"how much idle / where's my money / balance across wallets / grand total"* | `money` |
| *"are my strategies protected? / do they have a stop-loss? / how are my strategies doing / analyze my strategies / what's their DSL / mandate"* | `strategies` |
| *"analyze my positions / my positions vs the market / net exposure / concentration / idle drag"* | `positions` |
| *"analyze my portfolio / how am I doing"* (the full read) | `money` → `strategies` → `positions`, narrating between |

`--no-market` applies to every step (skips the `positions` market fan-out). Same fail-open contract as
`all`: each step returns valid JSON with `meta.warnings` on partial data and **never crashes on a
missing/corrupt state file, nor on a runtime CLI that is absent or cannot even be spawned** (it
self-heals by recomputing its prerequisites — every step also works STANDALONE, just slower; an
unreadable runtime costs you the runtime fields, which come back `null` + a warning, not the read). Keep `all` as the one-shot fallback when a single blocking call is fine; every
existing guardrail (the three-bucket taxonomy, `protected`, the mandate reads, multi-wallet grouping, the
DSL ladder + live tiers) holds identically across the steps and `all`.

## How to run the engine

```
python3 scripts/portfolio.py [money|strategies|positions|all] [--no-market] [--state PATH]
```

`all` is the default when no step is given — it composes every slice into one dict (the same output the
engine always produced). Prefer the **steps** above for a full read (they stream and don't trip the
timeout); use `all` only when a single blocking call is fine.

Returns `{totals, embedded_wallet, strategies, strategy_groups, exposure, signals, meta}`:
- `totals` — the three buckets + `grand_total_usd`, `unrealized_pnl`, and a `reconciles` flag (cross-
  checks the per-wallet sum against the portfolio aggregate; if `false`, say the numbers don't tie out
  and lead with the per-wallet figures).
- `embedded_wallet` — `address`, `idle_hl_usdc`, `evm_usdc[]` (per chain), `spot_usd`, `idle_total`.
- **`strategy_groups[]` — ONE entry per real strategy (a strategy is ALL its wallets). LEAD HERE.** The
  engine re-unites the per-wallet `strategies[]` rows into one group per strategy, keyed by
  `profile.group` (→ fallback `skill_name` → fallback the wallet). **This is the unit of analysis and
  recommendation** — present and reason at this level, never at individual wallets. Each group:
  - `label` — the group id (e.g. `ox`, `cougar`, `cub`); `skill_name`; `archetype` / `archetype_label`
    / `direction` (catalog facets when present).
  - `mandate` — the strategy's declared job (its `profile.description`, else `belief_plain`); shared by
    all instances. Judge the whole strategy against this.
  - `dsl` — the strategy's **DSL protection ladder** (how its DSL works), shared by all instances:
    `hard_stop_roe_pct` / `arm_at_roe_pct` / `tiers[]` / `has_phase2`, or `{preset_name, note}` for a
    named preset, or `null`. Surface it **once per strategy**; each open position's *live* tier is on
    `positions[].dsl`. See "DSL — how it works per strategy" above.
  - `is_multi_wallet` (bool) — `true` when the strategy spans >1 wallet (long+short, core+ballast,
    multi-sleeve). When true, the wallets are legs of ONE design — see "A strategy is ALL its wallets."
  - `instances[]` — the per-wallet detail: `name` (= `runtime_name`, e.g. `ox-core`), `wallet`,
    `wallet_short`, `account_value`, `idle_withdrawable`, `deployed`, `upnl`, `positions[]` (each with a
    live `dsl` tier object), `closed`.
  - `totals` — summed across every instance: `account_value`, `idle_withdrawable`, `deployed`, `upnl`,
    and `realized_pnl` (when available). **Report the strategy's figures from here, not per-wallet.**
  - `protected` (`true` / `false` / `null`) — `true` **only if ALL instances are protected**; `null` if
    ANY instance is `null` — an unread instance is never laundered into a `false`.
  - `flat_instances` — names of instances with **no open positions**. For a multi-wallet strategy these
    are the OTHER sleeve(s) **waiting for a signal** — NOT redeployable idle, never "dead money."
  - `profile_source` — where this strategy's profile came from (`registry` / `registry+catalog` / …).
- `strategies[]` — the per-wallet detail (kept for the bucket math + `exposure`; `strategy_groups[]` is
  the level you *present* from). Per wallet: `name`, `wallet`, `account_value`, `idle_withdrawable` (bucket 2 for
  *this* strategy), `deployed` (equity tied up in positions = account_value − withdrawable),
  `position_margin` (initial margin detail), `total_funded`/`total_withdrawn`, and:
  - `name` / `name_source` — the display name, and WHICH FIELD produced it. Chain:
    `strategyName` (its own name, `<id>-<instance>` for a package deploy) → `tradingStrategyName` (the
    package id) → `name` → the `"strategy"` placeholder (`name_source: null`). This is the instance
    label; `skill_name` is the package. **Trust `name` as a name only when `name_source` is
    `"strategyName"`.**
  - `skill_name` / `skill_version` — the strategy's package attribution (e.g. `ox`, `cougar`, `lion`),
    from its `strategy_list` record. `null` for a hand-rolled/custom strategy with no package.
  - `profile` — **the strategy's declared job, universal across ours + user-authored strategies.** Its
    load-bearing field is **`profile.description` — read from the strategy's DEPLOYED `runtime.yaml`**
    (the top-level folded `description:` the runtime registers), collapsed to a single line. Also from
    the runtime.yaml: `runtime_name`, `group`, `dsl_preset` (named preset string, or `true` for a
    bespoke inline preset), and **`dsl`** — the parsed **DSL protection ladder** (how DSL works for this
    strategy): `hard_stop_roe_pct` (phase1 floor, active from entry), `arm_at_roe_pct` (where the
    profit-ratchet arms = Tier 1), `tiers[]` (`{trigger_pct, lock_hw_pct}` profit-lock ladder), and
    `has_phase2`. For a **named-string preset** it's `{preset_name, note}` instead; `null` when the
    `exit:` block has no `dsl_preset`. This is the CONFIG side — pair it with each position's live `dsl`
    tier (see `positions[].dsl`). Optional **catalog enrichment** (templates only, keyed by `skill_name`;
    `null` for authored strategies): `belief_plain`, `thesis`, `archetype`, `sub_style`, `asset_classes`,
    `risk_level`, `time_horizon`, `tagline`. `profile.source` = `"registry"` / `"registry+catalog"` /
    `"catalog"`. **This is the yardstick — judge the strategy against `profile.description`, not memory
    and not a momentum benchmark.** `profile` is `null` only when the strategy is in neither the runtime
    registry nor the catalog (`meta.profile_source` records `registry`/`catalog`/`mixed`/`null`).
  - `protected` (`true` / `false` / `null`) — `true` only when the deployed `runtime.yaml`'s `exit:`
    block was actually READ by the engine (`dsl_preset` or `engine: dsl`); a `skill_name` attribution
    stamp alone no longer counts. `null` = the runtime read did not answer — say "could not verify on
    this host," never "protected" or "not protected." Config-level posture, not a live per-position
    check — see the tri-state rule above.
  - `closed` — `{realized_pnl, trade_count, recent[]}` from a read-guarded `discovery_get_trader_history`
    on the strategy wallet: `realized_pnl` (total booked PnL over the recent pull), `trade_count`, and
    `recent[]` (last few closed trades: `asset`, `direction`, `realized_pnl`, `entry_px`, `exit_px`,
    `closed_time`). On a read failure `realized_pnl` is `null` and a `meta.warnings` entry is added —
    treat as "realized PnL unavailable," never as zero.
  - `positions[]` (asset, dex, direction, leverage, notional, margin, `upnl`, `return_on_equity_pct`,
    `liq_px`, `market_24h_pct`, `vs_market`, and **`dsl`** — the live per-position ratchet tier).
    - **`dsl`** — this position's live DSL/ratchet state. **`armed: true`** → `tier_index`,
      `high_water_roe`, `status`, `locked` (= `lock_hw_pct` at the active tier). **`armed: false`** (no
      ratchet record — the position is sub-Tier-1) → `hard_stop_roe_pct`, `arm_at_roe_pct`, `roe`, and a
      `note` that reads "protected from entry by the phase1 hard stop; profit-ratchet arms at Tier 1
      (+X%) — currently +Y%." **`armed: false` means the profit-ratchet hasn't ARMED yet, NOT that the
      position is unprotected** — the phase1 hard stop protects it from entry. Never present it as "no
      DSL." If the ratchet read failed entirely, every position still gets this config-based `armed:
      false` object (+ a `meta.warnings` note).
- `exposure` — `net_notional_usd` + `net_bias`, gross long/short, `by_asset_net_usd`,
  `largest_position`.
- `signals` — `idle_drag_pct` (how much capital isn't working), `deployed_pct`,
  `largest_position_pct_of_deployed` (concentration).
- `meta` — `profile_source` (`registry` / `catalog` / `mixed` / `null` — where the strategies' mandates
  came from, in aggregate), `registry_source` (`"runtime-cli"` / `null` — `null` when the
  `openclaw senpi runtime list` read failed), **`runtime_read_ok`** (bool — `true` when that read
  answered, `false` when it failed; `false` is the whole-fleet "this box's runtime could not be asked"
  signal that pairs with the `meta.warnings` line naming the command), `catalog_source`
  (`local` / `remote` / `null`), `strategy_count`, **`has_multi_wallet_strategy`** (bool — `true` when at
  least one strategy spans multiple wallets/instances; a cue to reason at `strategy_groups[]` and apply
  the "a strategy is all its wallets" rules), and `warnings[]`.
- The engine **fails open** — partial data still returns valid JSON with `meta.warnings`. If the runtime
  registry is unreadable, mandates fall back to the catalog (templates only); if that's also gone,
  `profile` is `null` and you judge on behavior.

## Output contract

Order matters: **strategy verdicts lead; positions are evidence underneath them.** (When the question
is purely "how much / where is my money," you can open with the money map instead — but for anything
about "my strategies / how am I doing," lead with the per-strategy read.)

**Lead from `strategy_groups[]` — one verdict per real strategy, NOT per wallet.** A multi-wallet
strategy (long+short, core+ballast, multi-sleeve) is ONE strategy across N wallets; present it as one.
See "A strategy is ALL its wallets."

1. **Total + the three buckets.** `grand_total_usd`, broken into idle-in-embedded / idle-in-strategies
   / deployed — each labeled by *where*. Keep it tight; this is the money map, not the analysis.
2. **Per-strategy verdict (the real value).** For **each `strategy_groups[]` entry** (one per real
   strategy — never one per wallet), in this order:
   1. **Label + mandate.** The group's `label` and what it was deployed to *do* — from the group's
      `mandate` (its `profile.description`, read from the deployed `runtime.yaml`; add catalog facets
      like `belief_plain`/`archetype` when present). Works the same for a user-authored strategy. "cub
      is a K-shaped long/short dispersion book — long the structural winners, short the laggards; the
      P&L is the spread." **If `is_multi_wallet`, name it as ONE strategy across its sleeves** ("cougar
      is a market-neutral long/short pair") — never as two strategies.
   2. **Is it doing its job — against its OWN mandate.** Not vs a momentum benchmark, and **not vs the 4h
      leaderboard.** A hedge flat in calm, an all-weather core steady-not-flashy, a market-neutral book
      counter to the crowd, a selective strategy waiting with no position — all **working as designed**.
      See "Judge each strategy against its OWN mandate" and "Counter to smart money is not a defect."
   3. **Positions as evidence — across ALL the strategy's instances.** The open positions (from every
      instance in the group) that *show* it's on-mandate: direction, leveraged return
      (`return_on_equity_pct`), and **vs the market** (`market_24h_pct`, `vs_market`) — "short ETH, +11%
      on margin, *with* today's 4% selloff." A group instance in `flat_instances` is the strategy's
      **OTHER sleeve waiting for its signal** (for a multi-wallet strategy) or the whole strategy
      waiting for its setup (for a single-wallet one) — say that, never "dead money." Flag any position
      fighting the tape *for a directional-momentum mandate* / near `liq_px` / oversized.
   4. **PnL — realized + unrealized, summed across the strategy.** The group's `totals.realized_pnl`
      and `totals.upnl` (+ a couple of `closed.recent[]` trades from its instances). A flat sleeve may
      have already banked real gains on the other sleeve.
   5. **DSL protection — ladder + live tiers.** State the group's `protected` posture (⟹ **all**
      instances ship a DSL exit), then **how its DSL works** from `group.dsl` / `profile.dsl` (hard stop
      at `hard_stop_roe_pct`, ratchet arms at `arm_at_roe_pct`, the tier ladder), then **each open
      position's live tier** from `positions[].dsl` — "armed at Tier N, locked L% of peak" or, for a
      sub-Tier-1 position, "protected from entry, ratchet arms at +X% — currently +Y%." **Never say a
      live position has "no DSL" because it lacks a ratchet record** (sub-Tier-1 positions have none by
      design). See "DSL — how it works per strategy, and which position is in which tier."
   6. **Any lever is WHOLE-STRATEGY.** If you suggest close / pause / adjust-config / top-up, it applies
      to the **entire strategy (all its wallets)** — never one sleeve. And the lever is the STRATEGY,
      not a hand-picked position the scanner will just re-open. See the HARD rule under "A strategy is
      ALL its wallets" and "Recommend at the STRATEGY level."
3. **Portfolio-level read.** Net exposure (net long/short and by sector), concentration (largest
   position), idle drag (capital sitting in cash), and the overall posture — is this book hedged,
   directional, mostly in cash? Compare the net tilt to where the broader market is.
4. **The two CTAs** (next section).

Formatting: group by strategy (a `strategy_groups[]` entry = one strategy; show its instances as its
sleeves, not as peers); show `Δ%` and leveraged return; emoji sparingly (🟢/🔴 for green/red books).
Show strategy wallet addresses in short form (`0x35d1...acb1`) unless asked for full.

## Mandatory closing (verbatim)

> **1. Want me to rebalance or adjust any of these positions?**
> **2. Want me to put the idle capital to work in a new strategy?**

- **CTA 1 → strategy / position management.** For an **autonomous strategy**, route to the
  STRATEGY-level levers (`strategy_pause` / `strategy_update` config / `strategy_close` / `strategy_top_up`)
  and apply them to the **whole strategy (all its wallets)** — never to a single sleeve of a multi-wallet
  strategy, and never hand-close a position the scanner will just re-open. Only use per-position tools
  (`edit_position` / `close_position`) for a genuinely ad-hoc position the user placed by hand. Confirm
  before any change; never trade unprompted.
- **CTA 2 → deploy idle.** If there's meaningful **truly-free** idle capital (lead from
  `signals.idle_drag_pct` and `idle_in_embedded` — NOT a flat sleeve of a live multi-wallet strategy,
  which is committed), offer to hand it to **senpi-strategy-discover** / **senpi-strategy-author** — fund
  a new strategy from the embedded idle, or top up an existing *whole* strategy via `strategy_top_up`.
  Propose; never deploy without confirmation.

## Resilience (engine handles; narrate honestly)

- **Token app-scoped / no wallet data** → `meta.degraded`. Say you can't read the account with this
  token (it needs a USER-scoped token); don't report an empty portfolio as "$0."
- **A strategy's clearinghouse read failed** → it's in `meta.warnings`; that wallet's positions may be
  incomplete. Say so rather than implying it's flat.
- **A strategy's closed-history read failed** → `closed.realized_pnl` is `null` + a `meta.warnings`
  entry (`trader_history … failed/returned no data`). Report realized PnL as **unavailable** for that
  strategy — never as `$0`.
- **`totals.reconciles == false`** → the per-wallet sum and the portfolio aggregate disagree; the engine
  also appends a `TOTALS DO NOT RECONCILE` entry to `meta.warnings` quoting both figures and the gap.
  STOP and re-run first (see above); if it persists, surface it and trust the per-wallet (live) figures.
- **Never** report `total_withdrawable` as embedded idle, never skip a wallet, never skip the CTAs.

## Skill Attribution

Guide/analysis skill — it *reads* the account and *recommends*; it does not place a trade or move
funds. Attribution happens downstream when the execution tools / strategy skills act on a CTA.


## Install — both scripts are required

The engine is **two files** in `scripts/`: `portfolio.py` (the engine) and `mcp_client.py` (its vendored
MCP helper, imported at runtime). **Install the whole `scripts/` directory** — copying `portfolio.py`
alone fails with `No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
