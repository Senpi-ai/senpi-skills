# improve-trades engine output — field catalog

The shape of the JSON `scripts/review.py` prints. The runtime JSON you get back is largely self-describing — read this only when a field's meaning or a guardrail-relevant nuance is unclear.

```
window        { from, to, label, window_days, last_n }   # the review window

book          ONE list of every wallet — PRESENT FIRST, then the detail (the `strategies` step and `all`):
  rows[]      the Senpi main wallet, one per Senpi STRATEGY (all its wallets) and one per saved wallet, sorted
              value desc; ties by label then address; value unknown LAST. Never re-section by origin — kind
              is a column. The main wallet row: origin: main_wallet, label "Senpi main wallet", kind managed,
              holds: cash, value_usd = its idle cash (account_get_portfolio forceFetch — senpi-portfolio's
              idle_in_embedded; None = couldn't load, named in excludes), trade columns None,
              not_applicable [trades, open_positions, protection]; never compared, never in deep_dive:
    label (a packaged strategy: its package id), display_label (label + short address — or "N wallets" for a
    strategy of several — when two rows share a label), kind: managed | read_only,
    wallet (None on a strategy of several wallets), status (managed only; MIXED when its wallets differ),
    value_usd (None = couldn't load, never 0),
    strategy_group, wallet_count, wallets_loaded, wallets_couldnt_load,   # managed: the strategy's wallets
    strategy_wallets[] { label, wallet, status, value_usd, open_position_count, closed_trade_count,
                         realized_pnl, fees, trades_capped }   # managed: one per instance — strategies[] has its review
    value_read: ok | partial | unavailable | error,   # partial = only some of a strategy's wallets loaded
                                              # managed: clearinghouse accountValue (main+xyz, shared idle once)
                                              # read_only: state.totalValueUsd verbatim (unified-account aware)
    no_hl_activity (read_only: role MISSING → a real 0), closed_trade_count, realized_pnl, fees (GROSS PnL),
    trades_unknown (true → None counts, never 0), trades_capped (HL's 2000-fill ceiling → "at least"),
    open_position_count (None = couldn't read), unpriced_coins (read_only), access (read_only, verbatim),
    protection { kind: runtime_exit }                         # managed — the runtime's DSL exit
             | { kind: live_stops, FULL, PARTIAL, NONE }      # read_only — counts of positions; None = unread
    timing { measurable_closes, exits_ahead, exits_ahead_share }   # this wallet's own timing — comparison input
  total       ONE book total, subtotals beside it — quote `line` verbatim; never sum yourself, no "Combined" row:
    value_usd, realized_pnl, fees,            # sums of the KNOWN subtotals (fees None unless both known)
    managed   { wallet_count, wallets_loaded, value_usd, couldnt_load[], realized_pnl (= pnl_summary.realized),
                fees (= pnl_summary.fees), realized_pnl_closed_strategies, state: ok | unreadable }
    read_only { wallet_count, wallets_loaded, value_usd, couldnt_load[], realized_pnl, fees, unpriced_coins[],
                trades_unknown[], trades_capped[], state: ok | unavailable }   # NEVER deployable
    excludes  { couldnt_load[], unpriced_coins[], unreadable[] },
    line, note    # line: "Book value $X: managed by Senpi $A (n wallets) · read-only $B (…) …" — not Senpi performance;
                  # "(1 of 2 wallets)" when only some loaded; no read-only clause when no saved wallets
  comparison  { line (None → say nothing comparative), reason, metric, min_closes (= MIN_COMPARE_CLOSES, 8),
                wallets[] { label, kind, measurable_closes, exits_ahead, exits_ahead_share, trades_capped },
                not_compared[] { label, why } }   # 2+ wallets with 8+ measurable closes, else no line
  deep_dive   { question, order[] } | None   # >1 wallet with something to review (a value, a position, trades,
                                             # or unknown): "Which wallet do you want me to go deeper on: …?" —
                                             # asked once, at the END of the whole answer
  note
  # managed rows are the CURRENT strategies (closed ones are history — closed_strategies[]); the Senpi
  # aggregates below stay Senpi-only and unchanged — the managed subtotal quotes them.

trades[]      per CLOSED trade (from strategies of ALL statuses — a churned book's history is complete):
  asset, strategy_label, strategy_status, direction, leverage, entry_px, exit_px, open_time, close_time,
  realized_pnl,                           # strategy_status: ACTIVE|PAUSED = current book; else = HISTORY
  price_now, price_since_exit_pct,        # subsequent action (current price only, v1)
  if_held_delta_usd,                      # counterfactual — CONTEXT, not verdict (short-sign adjusted)
  exit_vs_hold: exit_ahead | held_higher | flat | unknown,   # NEUTRAL context (exit_ahead=got out ahead), NOT a grade
  exit_reason: { terminal, tier_index/tier_reached, high_water_roe, source },   # which DSL lever fired
  source: "telemetry" | "reconstructed" | "external_wallet"   # telemetry = exit_reason from the event log; else discovery+ratchet
  wallet_kind: "external"                # only on a saved wallet's row (absent on Senpi strategy rows)

external_wallets[]   per SAVED wallet (read-only, traded by hand) — its own read, never in the Senpi aggregates
                      (null = the saved wallets couldn't be loaded — never "none"):
  label, address, access (quote verbatim), closed_trades_unknown (true → couldn't read: counts/PnL None, never 0),
  fills_capped (true → HL's 2000-fill ceiling: "at least"), closed_trade_count, realized_pnl, fees,
  timing_summary (same shape as below, this wallet only), state_read: ok | error | unavailable, read_error,
  open_positions   # state.positions VERBATIM (camelCase: protection FULL|PARTIAL|NONE, stopOrders[], coveredSize); None = couldn't load

pnl_summary      TOTAL LEDGER — LEAD WITH THIS (realized closed + unrealized open):
  realized, unrealized (None = UNKNOWN read, not 0), total (None when unrealized UNKNOWN),
  realized_by_book{ current, closed },      # quote this split — never re-derive a closed-book figure
  unrealized_coverage{ read, current_strategies },
  unrealized_partial   # true → some wallets UNKNOWN; unrealized/total are a FLOOR ("at least $X, N of M") — HARD RULE 1

telemetry_availability   the 'undetermined ≠ all-clear' signal — READ IT FIRST (guardrail 6):
  status ∈ available | partial | undetermined | no_trades,
  streams_computed,                          # false → leaks/blocked/execution_quality/dsl_close_reason_mix zeros are UNKNOWN, not 'none'
  exit_attribution{ attributed, total, telemetry, ratchet, unknown }   # attributed ~0 → NO calibration diagnosis

timing_summary   PROCESS-framed COUNTS (never $/week; NEUTRAL, never a grade):
  trade_count, exits_ahead, exits_held_higher, exits_flat, exits_unknown,
  realized_pnl_total, if_all_reclosed_now_total (CONTEXT, symmetric — see guardrail 1), by_asset_class{}

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
    wallet, status, mandate, dsl, closed_trade_count, realized_pnl,
    unrealized_pnl,           # current open positions' unrealized — None = UNKNOWN read (never a fake 0)
    total_pnl,                # realized + unrealized (None when unrealized UNKNOWN) — JUDGE ON THIS, not realized
    open_position_count,
    open_positions[]{ asset, direction, unrealized_pnl, return_on_equity_pct, entry_px, position_value, leverage },
    on_mandate_note }         # open_positions = the 'are winners running' evidence (guardrail 1)
  # THIS is the verdict + improvement surface. Nothing here is closed.

closed_strategies[]  HISTORY ONLY (CLOSED / INACTIVE / … — churned or retired redeployments):
  { label, wallet_short, status, trade_count, realized_pnl }
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
`MANUAL_CLOSE`, `LIQUIDATED`, `ADL`. A saved wallet's trade is always `MANUAL_TRADE` (`source: "external_wallet"`)
— the user's own exit, never a DSL tier and never the same thing as `manual`/`MANUAL_CLOSE` (a Senpi strategy closed by
hand). Neither available → `UNKNOWN` (`source: "unknown"`) — say "exit mechanism
not recorded on this build," never guess. `tier_index`/`tier_reached` = the tier that locked; `high_water_roe`
= the peak ROE — together they tell you *which lever* to tune.
