# 🐺 DIRE v1.0 — BRENTOIL XYZ Specialist

First Kodiak-family port to a non-crypto asset class. Single-asset specialist
on `xyz:BRENTOIL`. News-driven momentum breakouts with aggressive early-tier DSL locks.

## Quickstart

### Install

```bash
cd /data/workspace/skills/
git clone <senpi-skills-repo>  # or equivalent pull mechanism
# Ensure /data/workspace/skills/dire-strategy/ exists with this tree
```

### Configure

Edit `config/dire-config.json`:

```json
{
  "wallet": "0x...",
  "strategyId": "<uuid-from-strategy-create>",
  "startingBudget": 1000,
  "minScore": 9,
  ...
}
```

Alternatively set environment variables (take precedence over config file):

```bash
export DIRE_WALLET=0x...
export DIRE_STRATEGY_ID=...
```

### Create strategy on Senpi

```bash
mcporter call senpi strategy_create_custom_strategy --args '{
  "initialBudget": 1000,
  "positions": [],
  "strategyName": "dire-brentoil-v1",
  "skillName": "dire-strategy",
  "skillVersion": "1.0"
}'
```

Record the returned `strategyId` and `strategyWalletAddress` into
`config/dire-config.json`.

### Enable position tracker runtime

```bash
openclaw senpi runtime install --config runtime.yaml
openclaw senpi runtime list  # verify dire-tracker appears
```

This starts the position_tracker + DSL exit engine. The DSL ladder in
`runtime.yaml` must match the `dslTiers` in `config/dire-config.json`.

### Register scanner cron

Scan cadence is 60 seconds:

```bash
openclaw cron add dire-scanner "* * * * * python3 /data/workspace/skills/dire-strategy/scripts/dire-scanner.py"
```

### Verify

Manual scan:

```bash
python3 /data/workspace/skills/dire-strategy/scripts/dire-scanner.py
```

Expected output shapes:

- No position open, market quiet:
  ```json
  {"status":"ok","heartbeat":"NO_REPLY","note":"HUNTING: gate_blocked 4TF_MISALIGNED:...","version":"1.0"}
  ```
- No position open, score below threshold:
  ```json
  {"status":"ok","heartbeat":"NO_REPLY","note":"HUNTING: score_low 7/9","reasons":[...],"version":"1.0"}
  ```
- Entry taken:
  ```json
  {"status":"ok","action":"ENTRY","direction":"LONG","score":10,"reasons":[...],"execution":{...},"dsl":{"attached":true,...}}
  ```
- Position already open (scanner does not exit):
  ```json
  {"status":"ok","heartbeat":"NO_REPLY","note":"RIDING: position_open coin=BRENTOIL direction=LONG upnl=$X roe=0.07..."}
  ```

## Architecture

See `SKILL.md` for full architecture doc. Summary:

- Single-asset specialist on `xyz:BRENTOIL`
- 3-mode state machine (HUNTING / RIDING / STALKING)
- 60s scan cadence
- 4TF alignment hard gate (5m/15m/1h/4h all same direction)
- SM HARD BLOCK (Smart Money direction must align)
- OI velocity scoring (flat-path from day 1 — no nested-path bug)
- Volume spike scoring (news-impact proxy)
- Price cleanliness gate (no adverse wicks in last 30 min)
- Drawdown circuit breaker (15% from rolling 7-day peak)
- Daily entry cap (2 per 24h)
- Wolverine execution pattern (mcporter CLI direct, no LLM parse loop)
- 5-tier DSL ladder with aggressive early lock (T0 at +5% → 25% HW)

## Sizing (v1.1 — conviction-scaled)

Leverage and margin scale with score. Higher conviction = bigger position.

| Score | Leverage | Margin % | Notional / account | Tier |
|---|---|---|---|---|
| 9 | 3x | 20% | 0.6x | cautious |
| 10 | 5x | 25% | 1.25x | standard |
| 11 | 7x | 30% | 2.1x | conviction |
| 12+ | 10x | 30% | 3.0x | apex |

Hard caps:
- absolute max leverage: **10x** (50% of Hyperliquid's 20x BRENTOIL max)
- leverageType: **ISOLATED** (XYZ requirement)
- max positions: **1**
- max notional/account value: **3.0x** (at apex tier)

## Key files

- `SKILL.md` — full architecture and rules
- `runtime.yaml` — DSL tier ladder + position tracker config
- `config/dire-config.json` — wallet, strategyId, tunables
- `scripts/dire_config.py` — MCP helpers, state I/O, validation
- `scripts/dire-scanner.py` — main scanner loop
- `state/` — runtime state (created on first scan)
  - `trade-counter.json` — daily entries, 7-day peak, drawdown gate state
  - `state.json` — scanner mode, last scan timestamp

## Monitoring

### First scan checklist

1. Scanner exits cleanly (`echo $?` = 0)
2. JSON emitted to stdout (single line)
3. If entry taken, `execution.fill_size > 0` AND `dsl.attached == true`
4. If entry taken, verify position appears in `strategy_get_clearinghouse_state`
5. If entry taken, verify DSL record appears in `ratchet_stop_list`

### 72-hour success criteria

**Green:** ≥1 entry attempted, DSL attached on every position, no ghost trades, equity ≥ 95%.

**Yellow:** Scanner running every 60s but no valid setups (market not producing oil momentum). Acceptable for 3 days; review if still zero at 7 days.

**Red:** Ghost trade (create_position success but no clearinghouse position), DSL attach failure, drawdown > 15%.

### Manual drawdown unlock

If the drawdown circuit breaker triggers and you want to override:

```bash
# Edit state/trade-counter.json
# Set "dd_manual_unlock": true
```

Scanner will skip the drawdown gate on next scan.

## Troubleshooting

### Scanner always outputs `HUNTING: gate_blocked 4TF_MISALIGNED`

Oil is in a sideways regime. No action — wait for trending setup. If > 72h
without any scan showing aligned setup, check if MIN_SCORE is too restrictive.

### Scanner always outputs `SM_HARD_BLOCK: no_mark_px`

Premium data is missing from asset context. Check if `market_get_asset_data`
is returning the full response (candles, asset_context, oi_velocity). If not,
restart the scanner cron and the MCP connection.

### `oi_velocity` is null on every scan

XYZ OI pollers may need warmup time. This is logged but not blocking — OI
velocity is a soft-score signal, not a hard gate. Null treats as pass.

### DSL attach fails after successful entry

Scanner automatically closes the position via `close_position` market order.
Inspect the JSON output — status will be `critical` with `DSL_ATTACH_FAILED_EMERGENCY_CLOSE`.
Investigate `ratchet_stop_add` MCP tool state before re-enabling.

## What this experiment proves/disproves

**Green within 30 days:**
- Kodiak-family architecture transfers to non-crypto assets
- Unlocks future XYZ specialists (NVDA, GOOGL, COPPER, etc.)

**Red:**
- XYZ markets have structural differences requiring different architecture
- $1,000 cost for a clear learning outcome

## References

- `references/skill-attribution.md` — credits and lineage
- Senpi MCP docs — https://docs.senpi.ai

## License

MIT
