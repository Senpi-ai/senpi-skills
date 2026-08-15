# improve-trades engine output — field catalog

The shape of the JSON `scripts/review.py` prints. The runtime JSON you get back is largely self-describing — read this only when a field's meaning or a guardrail-relevant nuance is unclear.

```
window        { from, to, label, window_days, last_n }   # the review window

trades[]      per CLOSED trade (from strategies of ALL statuses — a churned book's history is complete):
  asset, strategy_label, strategy_status, direction, leverage, entry_px, exit_px, open_time, close_time,
  realized_pnl,                           # strategy_status: ACTIVE|PAUSED = current book; else = HISTORY
  price_now, price_since_exit_pct,        # subsequent action (current price only, v1)
  if_held_delta_usd,                      # counterfactual — CONTEXT, not verdict (short-sign adjusted)
  exit_vs_hold: exit_ahead | held_higher | flat | unknown,   # NEUTRAL context (exit_ahead=got out ahead), NOT a grade
  exit_reason: { terminal, tier_index/tier_reached, high_water_roe, source },   # which DSL lever fired
  source: "telemetry" | "reconstructed"   # telemetry = exit_reason came from the event log; else discovery+ratchet

pnl_summary      TOTAL LEDGER — LEAD WITH NET (net_realized closed + unrealized open); gross_* is pre-fee:
  gross_realized, fees (None = UNDETERMINED, fills read failed — NOT 0), net_realized (gross − fees; None when fees undetermined),
  fees_status ("ok" | "undetermined"), fees_coverage {resolved, total} (partial → "net for R of T"), unrealized (None = UNKNOWN read, not 0),
  gross_total, net_total (None when unrealized OR fees UNKNOWN),   # LEAD with net_total, not gross_total
  realized_by_book{ current, closed },      # quote this split — never re-derive a closed-book figure
  unrealized_coverage{ read, current_strategies },
  unrealized_partial   # true → some wallets UNKNOWN; unrealized/total are a FLOOR ("at least $X, N of M") — HARD RULE 1

telemetry_availability   the 'undetermined ≠ all-clear' signal — READ IT FIRST (guardrail 6):
  status ∈ available | partial | undetermined | no_trades,
  streams_computed,                          # false → leaks/blocked/execution_quality/dsl_close_reason_mix zeros are UNKNOWN, not 'none'
  exit_attribution{ attributed, total, telemetry, ratchet, unknown }   # attributed ~0 → NO calibration diagnosis

timing_summary   PROCESS-framed COUNTS (never $/week; NEUTRAL, never a grade):
  trade_count, exits_ahead, exits_held_higher, exits_flat, exits_unknown,
  gross_realized_pnl_total, fees_total (None = UNDETERMINED, NOT 0), net_realized_pnl_total (LEAD with this), fees_status, fees_coverage {resolved, total},
  if_all_reclosed_now_total (CONTEXT, symmetric — see guardrail 1), by_asset_class{}   # by_asset_class carries gross/fees/net per class

dsl_close_reason_mix   "shaken out too early / how are my exits firing" (from trades[] exit_reason):
  overall        { by_terminal{}, trade_count, premature_exits }
  by_asset_class { crypto|equity/index: {…same…} }
  by_strategy    { <strategy_label>: {…same…} }   # filter by label → "why is [strategy] losing"
  premature_exit_samples[], premature_note        # premature = trailing_floor/weak_peak/max_retrace OR low-tier+small-ROE

blocked_summary   "what did my own limits block" (from missed_signals[]):
  total_blocked, by_reason_code{ no_slots|no_margin|risk_gate_*|asset_banned|… },
  by_strategy{ <strategy_label>: { reason_code: n } }

leaks   "where am I leaking" (telemetry event scan — fail-open to zeroed):
  order_failed     { count, samples[] { asset, reason, ts, strategy_label } }   # order rejected → $ never entered
  protection_gaps  { count, samples[] { asset, event, … } }                     # dsl.sl_sync_failed/handoff → naked leg
  risk_halts       { count, samples[] { reason, … } }                           # runtime.paused → trading stopped

execution_quality   "fees — maker vs taker" (from order.filled execution_as_maker):
  maker_fills, taker_fills, unknown_fills, maker_ratio,   # RATE only
  authoritative_fee_note                                  # the future ledger fee-$ hook (NOT called per-trade)

book_vs_market   the "what did I miss" gap:
  top_movers[] { asset, asset_class, pct, smart_money_pct, trader_count },
  participation[] { asset, held, side, aligned },     # was the book on the right side?
  gaps[]          { asset, pct, ... }                 # movers the book had NO exposure to
  window                                              # the leaderboard's rolling window (e.g. "4h")

strategies[]  the CURRENT book ONLY (status ACTIVE | PAUSED) — each judged vs ITS mandate, on TOTAL PnL:
  { label,                    # its own name (strategyName), or the package id when it has none
    group,                    # the runtime.yaml's strategy key — SAME on every sleeve of one strategy
    skill_name,               # the package attribution stamp — same pairing, survives a dead registry
    # group/skill_name are the ONLY proof two rows are one strategy: sleeves are named apart and carry
    # different mandates. Two ACTIVE rows sharing either one are sleeves — never merge or close one.
    wallet, status, mandate, dsl, closed_trade_count,
    gross_realized_pnl,       # HL closedPnl — GROSS (pre-fee)
    fees, net_realized_pnl, fees_status, fees_coverage {resolved, total},   # fees from the fills ledger (None = UNDETERMINED, NOT 0); net = gross − fees; coverage = trades whose fee resolved
    unrealized_pnl,           # current open positions' unrealized — None = UNKNOWN read (never a fake 0)
    gross_total_pnl,          # gross_realized + unrealized (None when unrealized UNKNOWN)
    net_total_pnl,            # net_realized + unrealized (None when either UNKNOWN) — JUDGE ON THIS, not gross
    open_position_count,
    open_positions[]{ asset, direction, unrealized_pnl, return_on_equity_pct, entry_px, position_value, leverage },
    on_mandate_note }         # open_positions = the 'are winners running' evidence (guardrail 1)
  # THIS is the verdict + improvement surface. Nothing here is closed.

closed_strategies[]  HISTORY ONLY (CLOSED / INACTIVE / … — churned or retired redeployments):
  { label, wallet_short, status, trade_count, gross_realized_pnl, fees, net_realized_pnl, fees_status, fees_coverage }
  # deliberately NO mandate / dsl / verdict / on_mandate_note. Their trades are already in trades[]
  # (part of the timing review, attributed by label). NEVER give these a "consolidate/kill/fix" verdict,
  # NEVER flag their absent mandate as a bug, NEVER count them as live "wallets to consolidate."

meta          { warnings[], sources[], window, degraded,
                strategy_count,             # every enumerated strategy (all statuses) — a raw total
                current_strategy_count,     # the LIVE book — THIS is "how many strategies you run"
                closed_strategy_count,      # churned/closed redeployments — HISTORY, not live redundancy
                trade_count,
                telemetry_source,           # available | partial | unavailable — how much enrichment landed
                exit_reason_source_counts,  # { telemetry, ratchet, unknown } — where each exit_reason came from
                missed_signal_count, leak_counts }   # quick glances at the telemetry streams
```

`exit_reason.terminal` — **when telemetry enriched it** (`source: "telemetry"`) it's the native
`close_reason`: `tier_breach`, `max_retrace`, `trailing_floor`, `weak_peak`, `dead_weight`, `hard_timeout`,
`manual`, `sl_hit`. **When it fell back to the ratchet record** (`source: "ratchet"`) it's `SL_TRIGGERED`,
`MANUAL_CLOSE`, `LIQUIDATED`, `ADL`. Neither available → `UNKNOWN` (`source: "unknown"`) — say "exit mechanism
not recorded on this build," never guess. `tier_index`/`tier_reached` = the tier that locked; `high_water_roe`
= the peak ROE — together they tell you *which lever* to tune.
