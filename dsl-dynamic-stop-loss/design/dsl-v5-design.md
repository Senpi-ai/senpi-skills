# DSL v5 Design Document

**Scope:** Four targeted changes to `dsl-dynamic-stop-loss`:
1. Strategy-scoped state file grouping
2. Cleanup on position and strategy close
3. MCP-based price fetching (main + xyz dex)
4. Agent skills standards compliance

---

## 1. Strategy-Based State File Grouping

### Problem

Multiple strategies can hold the same asset simultaneously — e.g., two strategies both running ETH LONG. With the current flat `DSL_STATE_FILE=/path/to/state.json` convention, these collide unless callers manually pick distinct filenames with no enforced structure. Different positions within a strategy may also have different DSL settings (5% stop vs 20%), so state must be isolated per position _within_ a strategy.

### Solution: Directory-Per-Strategy Convention

Enforce a two-level directory layout for all DSL state files:

```
/data/workspace/dsl/
  {strategyId}/
    {asset}.json          # one state file per position per strategy
```

Examples:
```
/data/workspace/dsl/
  strat-abc-123/
    ETH.json              # ETH LONG @ 5% stop, 10x leverage
    BTC.json              # BTC LONG @ 10% stop, 5x leverage — different settings, same strategy
  strat-xyz-456/
    ETH.json              # ETH LONG @ 20% stop — independent from strat-abc's ETH, different settings
    SOL.json
```

Each `{asset}.json` file is fully self-contained with its own DSL configuration (tiers, retrace thresholds, floors, etc.). Two positions within the same strategy can have completely different settings — the file-per-position structure enforces this isolation naturally. Two strategies holding the same asset (e.g., both `strat-abc-123/ETH.json` and `strat-xyz-456/ETH.json`) are also fully isolated with no shared state.

For xyz dex assets (equities/metals), the colon is replaced with a double-dash to stay filesystem-safe:
```
/data/workspace/dsl/
  strat-abc-123/
    xyz--SILVER.json      # represents xyz:SILVER
```

### Environment Variable Changes

| Variable | v4 | v5 |
|---|---|---|
| `DSL_STATE_FILE` | Full path (v4) | **Removed** in v5 — use strategy dir + asset |
| `DSL_STATE_DIR` | — | Base directory (default: `/data/workspace/dsl`) |
| `DSL_STRATEGY_ID` | — | Strategy UUID (required if not in state file path) |
| `DSL_ASSET` | — | Asset symbol (required if not using `DSL_STATE_FILE`) |

**Preferred invocation (v5):**
```
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 DSL_ASSET=ETH python3 scripts/dsl-v5.py
```

The script resolves the state file path as:
```python
state_file = f"{DSL_STATE_DIR}/{DSL_STRATEGY_ID}/{asset_to_filename(DSL_ASSET)}.json"
```

v5 requires `DSL_STRATEGY_ID` and `DSL_ASSET` (no legacy single-file path).

### State File Schema Changes

No schema changes. `strategyId` already exists as a required field and continues to serve as the canonical strategy identifier. The directory path is the authoritative grouping key, not just the field value.

### Cron Setup (v5)

```
# Position A — strat-abc / ETH
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 DSL_ASSET=ETH python3 scripts/dsl-v5.py

# Same asset, different strategy — fully isolated
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-xyz-456 DSL_ASSET=ETH python3 scripts/dsl-v5.py
```

---

## 2. Cleanup on Position and Strategy Close

### Problem

Currently there is no automated cleanup. Closed positions leave state files on disk indefinitely. Inactive strategies accumulate directories. The agent must manually track what to clean up.

### Two-Level Cleanup Model

#### Level 1: Position Close

Triggered when `closed=true` in the script output (position breach + successful close, or explicit deactivation).

**Action:** Delete the state file (no archiving).

```
Before close:
  /data/workspace/dsl/strat-abc-123/ETH.json

After close:
  (file removed)
```

**When performed:** The main `dsl-v5.py` script handles this automatically on close, immediately after setting `active=false`.

#### Level 2: Strategy Close

Triggered when an entire strategy is deactivated — all positions within it are closed, or the agent explicitly signals strategy shutdown.

**New script:** `scripts/dsl-cleanup.py`

```
DSL_STATE_DIR=/data/workspace/dsl DSL_STRATEGY_ID=strat-abc-123 python3 scripts/dsl-cleanup.py
```

This script:
1. Checks all `*.json` files in the strategy directory
2. If any `active=true` files remain → exits with warning (safety guard, no cleanup)
3. If all positions are closed (or directory is empty) → deletes the entire strategy directory (no archiving).

**Output JSON** (printed to stdout for agent consumption):
```json
{
  "status": "cleaned",
  "strategy_id": "strat-abc-123",
  "positions_deleted": 3,
  "blocked_by_active": [],
  "time": "2026-02-27T15:30:00Z"
}
```

or if blocked:
```json
{
  "status": "blocked",
  "strategy_id": "strat-abc-123",
  "blocked_by_active": ["ETH", "BTC"],
  "time": "2026-02-27T15:30:00Z"
}
```

### Agent Responsibilities for Cleanup

| Event | Agent action |
|---|---|
| `closed=true` in dsl-v5.py output | Disable this position's cron job; script already deleted state file |
| All cron jobs for strategy disabled | Call `dsl-cleanup.py` for strategy-level cleanup |
| `strategy_close_strategy` called | Call `dsl-cleanup.py` after verifying all positions reported `closed=true` |

### File Structure After Cleanup

Closed positions and fully closed strategies are deleted (no `_closed/` archive). Only active strategy directories and their active position files remain:

```
/data/workspace/dsl/
  strat-abc-123/               # active strategy
    ETH.json
```

---

## 3. MCP-Based Price Fetching

### Problem

Current code uses a direct HTTP call to `https://api.hyperliquid.xyz/info` (allMids). This:
- Only covers the main Hyperliquid DEX
- Does not support xyz dex assets (equities, metals — prefixed `xyz:SILVER`, `xyz:AAPL`, etc.)
- Bypasses the established MCP abstraction layer

### MCP Tool

Use `senpi:market_get_prices` via `mcporter`:

```bash
mcporter call senpi market_get_prices --args '{"assets": ["ETH"], "dex": ""}'
```

**MCP tool parameters:**
| Parameter | Type | Description |
|---|---|---|
| `assets` | string[] (optional) | Filter to specific assets; omit for all |
| `dex` | string | `""` for main DEX, `"xyz"` for xyz DEX |

**Response structure:**
```json
{
  "prices": { "ETH": "2850.5", "BTC": "68000.0" },
  "count": 2
}
```

**Alternative (allMids) MCP response format** (confirmed: flat object, no wrapper; coin → mid price string; optional `dex`). Main DEX includes symbol keys (e.g. `"ETH"`, `"BTC"`) and index keys (`"@1"`–`"@295"`). Look up by bare symbol.
```ts
interface AllMidsResponse { [coin: string]: string; }
getAllMids(dex?: string): Promise<AllMidsResponse>
```

### Asset Detection Logic

The `asset` field in the state file uses the `xyz:` prefix to encode which dex a position belongs to. The MCP tool does **not** use this prefix — the `dex` parameter selects the exchange, and token names in both the request filter and the response price map are always bare symbols (no prefix).

| State file `asset` | MCP `dex` param | MCP `assets` filter | Response price key |
|---|---|---|---|
| `"ETH"` | `""` (main) | `["ETH"]` | `"ETH"` |
| `"BTC"` | `""` (main) | `["BTC"]` | `"BTC"` |
| `"xyz:SILVER"` | `"xyz"` | `["SILVER"]` | `"SILVER"` |
| `"xyz:AAPL"` | `"xyz"` | `["AAPL"]` | `"AAPL"` |

Detection:
```python
if asset.startswith("xyz:"):
    dex = "xyz"
    lookup_symbol = asset.split(":", 1)[1]   # "xyz:SILVER" → "SILVER"
else:
    dex = ""
    lookup_symbol = asset                     # "ETH" → "ETH"
```

### Mcporter Call Pattern

```python
result = subprocess.run(
    ["mcporter", "call", "senpi", "market_get_prices",
     "--args", json.dumps({"assets": [lookup_symbol], "dex": dex})],
    capture_output=True, text=True, timeout=15
)
mcp_response = json.loads(result.stdout)
# Response price map keys are bare symbols regardless of dex
price = float(mcp_response["prices"][lookup_symbol])
```

### close_position Call for xyz Assets

When closing xyz assets, the `coin` parameter for `close_position` should use the full prefixed name (`xyz:SILVER`), matching how Hyperliquid identifies the position on its API. This is consistent with how strategy tools reference coins.

```python
coin = state["asset"]   # "xyz:SILVER" — pass as-is to close_position
```

### Error Handling

MCP call failures follow the same pattern as v4:
- `consecutiveFetchFailures` counter increments
- Output `status: "error"` with `consecutive_failures` count
- Auto-deactivate after `maxFetchFailures` consecutive failures

---

## 4. Agent Skills Standards Compliance

### SKILL.md Frontmatter

Update to full compliance with the [agent skills spec](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview):

```yaml
---
name: dsl-dynamic-stop-loss
description: >-
  Manages automated trailing stop losses for leveraged perpetual positions on
  Hyperliquid, including xyz dex equity and metal instruments. Monitors price
  via cron, ratchets profit floors through configurable tiers, and auto-closes
  positions on breach via MCP. Supports LONG and SHORT, strategy-scoped state
  isolation, and automatic cleanup on position or strategy close.
  Use when protecting an open Hyperliquid perp position, setting up trailing
  stops, managing profit tiers, or automating position exits.
license: Apache-2.0
compatibility: >-
  Requires python3, mcporter (configured with Senpi auth), and cron.
  Hyperliquid perp positions only (main dex and xyz dex).
metadata:
  author: jason-goldberg
  version: "5.0"
  platform: senpi
  exchange: hyperliquid
---
```

Key changes:
- `description` written in third person, under 1,024 characters
- Includes both what it does and when to use it
- States both main and xyz dex support
- Mentions strategy-scoped isolation and cleanup

### MCP Tool References

All MCP tool references in SKILL.md must use **fully qualified names** (`server:tool_name`):

| Old reference | New reference |
|---|---|
| `market_get_prices` | `senpi:market_get_prices` |
| `close_position` | `senpi:close_position` |
| `market_list_instruments` | `senpi:market_list_instruments` |

### File Structure (v5)

```
dsl-dynamic-stop-loss/
├── SKILL.md                      # Main instructions (keep under 500 lines)
├── design/
│   └── dsl-v5-design.md          # This document
├── scripts/
│   ├── dsl-v5.py                 # Core DSL engine (updated)
│   └── dsl-cleanup.py            # New: strategy/position cleanup
└── references/
    ├── state-schema.md           # Updated with v5 path conventions
    ├── output-schema.md          # Updated with cleanup output fields
    ├── tier-examples.md          # No change
    └── customization.md          # No change
```

### SKILL.md Content Reorganization

The SKILL.md needs reorganization to stay under 500 lines and follow progressive disclosure:
- Keep core concepts, architecture, and quick-start in SKILL.md
- Move detailed schema to `references/state-schema.md` (already done)
- Move cleanup details to `references/cleanup.md` (new)
- Reference files with explicit links, not inlined content

### Script Standards

Per the skills guide, scripts must:
- Handle errors explicitly (already done in v4, preserve in v5)
- Document all constants at the top (already done)
- Use Unix-style paths
- Output deterministic JSON (already done)

---

## Implementation Plan

### Files to Create
- `design/dsl-v5-design.md` (this file)
- `scripts/dsl-cleanup.py`
- `references/cleanup.md`

### Files to Modify
- `scripts/dsl-v4.py` → `scripts/dsl-v5.py` (or rename in-place)
- `SKILL.md` (frontmatter + content updates)
- `references/state-schema.md` (add v5 path conventions)
- `references/output-schema.md` (add cleanup output fields)

### Compatibility
- v4 state file JSON schema is unchanged — no migration of file contents
- Invocation is v5-only: `DSL_STATE_DIR` + `DSL_STRATEGY_ID` + `DSL_ASSET` (no `DSL_STATE_FILE`)

---

## Open Questions

1. ~~**1st check — MCP configuration close position**~~ **Resolved:** `senpi:close_position` requires the `xyz:` prefix in the coin name (e.g. `coin: "xyz:SILVER"`). Pass `state["asset"]` as-is.

2. ~~**MCP response format**~~ **Resolved (allMids):** The **allMids** response is a flat object — no wrapper. Shape: `{ [coin: string]: string }` (coin → mid price string). Main DEX keys include symbol names (e.g. `"BTC"`, `"ETH"`, `"SOL"`) and index-style keys (e.g. `"@1"`–`"@295"`). DSL should look up by the asset’s key (bare symbol for main dex; for xyz dex use `dex` param and look up by bare symbol in that response). Optional `dex` param: `getAllMids(dex?: string) => Promise<AllMidsResponse>`. If using `market_get_prices` instead, verify its envelope separately (e.g. `{"prices": {...}, "count": N}`).
