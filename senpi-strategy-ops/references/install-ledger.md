# Install ledger — an install-time scratchpad (NOT a durable registry)

The install ledger is a **best-effort, install-time-only** record. It is written during an install run
to aid logging/partial-visibility, but **nothing depends on it persisting** — it may be absent on the
next session or after a host reimage. **Do not treat it as the source of truth for what's deployed.**

## The durable source of truth is LIVE SYSTEM STATE

| Question | Answer from |
|---|---|
| Is a runtime already deployed for this wallet? | `SenpiClient.is_runtime_registered(wallet)` / `openclaw senpi runtime list` |
| Which scanner daemons are running, and for which wallet/scanner? | `senpi_runtime_helpers.state.list_daemons()` → `read_pid(name)` (has `wallet` + `scanner`) |
| Which strategy wallets exist, and which strategy created them? | MCP `strategy_list` (`tradingStrategyName` = the package `id`/`skillName`; `strategyWalletAddress`) |

So the lifecycle operations derive everything from the package + live state, **ledger-free**:

- **Idempotency** (`install`): before deploying an instance, check `is_runtime_registered(wallet)`.
  If a runtime already exists and `--reinstall` is not set → report `already_installed` and skip. No
  ledger read.
- **Teardown** (`uninstall`): reads the **package**, not the ledger. `runtime_id` = the `runtime.yaml`
  top-level `name:` (static) → `runtime delete`. The daemon = the running daemon whose
  `pid.json.scanner` == the instance's `scanner.name` → `stop_pid` (wallet read from that pid.json).
  Works even if the ledger is gone.
- **Wallet recovery** (agent, on a fresh session): `strategy_list` matched on `tradingStrategyName`
  → `strategyWalletAddress`. Never blindly create a new funded wallet; confirm with the user first.

## Format (when present)

`/data/.openclaw/senpi-install-ledger.json` (override `SENPI_INSTALL_LEDGER`); `(id, instance) →
{wallet, phase, runtime_id, daemon}` where `phase ∈ wallet_ready → runtime_created → daemon_launched
→ verified`. Treat any read as advisory only — always reconcile against live state before acting.
