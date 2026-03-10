# MCP: Closing a Single Position

Use this when the agent or a job must close **one** position (manual close, FLIP_NOW, rotation, or retry after DSL `pending_close`).

**DSL cron (dsl-v5.py)** uses the same method for all position closes: breach, Phase 1 time-based cuts (hardTimeout, weakPeakCut, deadWeightCut), and pending-close retries all call **`close_position`** with `strategyWalletAddress`, `coin`, and `reason` (no separate path for phase1 options).

## Correct tool: `close_position`

For closing a **single** position, call the Senpi MCP tool **`close_position`** (not `strategy_close_positions` or `strategy_close`).

**Arguments:**

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `strategyWalletAddress` | string | Yes | Strategy wallet address (from `strategy_get` or DSL state file `wallet`) |
| `coin` | string | Yes | Asset symbol, e.g. `ETH`, `HYPE`, or `xyz:SILVER` for xyz dex |
| `reason` | string | Yes | Human-readable reason (e.g. `manual close`, `rotation_for_stronger_signal`, `FLIP_NOW`) |

**Example (mcporter):**

```bash
mcporter call senpi close_position --args '{"strategyWalletAddress":"0x...","coin":"HYPE","reason":"manual close"}'
```

**Example (from script):**

```python
mcporter_call("close_position",
              strategyWalletAddress=wallet,
              coin="HYPE",  # or "xyz:SILVER" for xyz
              reason="manual close")
```

## Why manual close may fail: wrong tool in job spec

If the **job spec** (e.g. OpenClaw job or agent MCP config) is set to use **`strategy_close_positions`** for manual single-position close, it will fail or behave wrongly:

| Tool | Purpose | Arguments (typical) | Use for single-position close? |
|------|----------|----------------------|---------------------------------|
| **`close_position`** | Close **one** position | `strategyWalletAddress`, `coin`, `reason` | **Yes** |
| `strategy_close_positions` | Close **all** positions in a strategy (batch) | `strategyId`, `coins: []` | No — different API and semantics |
| `strategy_close` | Close the **strategy** (funds to main wallet) | `strategyId` | No — tears down the whole strategy |

**Check the job spec:**

1. **Tool name** must be **`close_position`** (not `strategy_close_positions`).
2. **Arguments** must include **`strategyWalletAddress`** (wallet), **`coin`** (asset), **`reason`** (string). Using `strategyId` instead of `strategyWalletAddress` is a common mistake — the close API expects the wallet address.
3. **Coin format:** main dex use `ETH`, `HYPE`; xyz dex use `xyz:SILVER`, `xyz:GOLD`, etc.

If the job is triggered from DSL output (e.g. `pending_close: true`), the agent should read `wallet` and `asset` from the DSL state file or from the strategy, then call `close_position(strategyWalletAddress=wallet, coin=asset, reason="...")`.

## Response: CLOSE_NO_POSITION

If the position was already closed (e.g. SL filled, or closed manually elsewhere), the API may return **CLOSE_NO_POSITION**. The DSL cron treats this as success and archives the state with the appropriate `closeReason`. The agent does not need to retry; the position is already closed.
