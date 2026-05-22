# Strategy Creation — the fast path

**Read THIS doc, then build. It's self-contained — you should not need to fetch other files to produce a working strategy.** (Deep references are linked at the bottom for edge cases only.)

A Senpi strategy is **a Python producer that emits signals + a `runtime.yaml` that tells the runtime how to act on them.** The runtime owns execution, exits (DSL), and risk. You write the *signal logic*; you do **not** write execution, stops, or a daemon loop.

> **The invariant — non-negotiable.** The producer ONLY emits signals via `push_signal`. It NEVER calls `create_position`/`strategy_create_custom_strategy`, NEVER writes its own stop/exit logic, NEVER hand-rolls a daemon loop or risk checks. A "custom strategy" = custom *signal logic* in a standard producer, not a custom harness. If you're calling `create_position` in the producer, stop — that's the runtime's job.

---

## The 5 steps

1. **Pick your archetype + example + DSL preset** (table below). Clone the example agent's three files as your starting point.
2. **Write the producer** (`scripts/<skill>-producer.py`) on the bundled SDK — skeleton below.
3. **Write `runtime.yaml`** — complete minimal template below.
4. **Write `config/<skill>-config.json`** — operator-tunable defaults (wallet, chatId, model via env).
5. **Verify** the daemon is alive + ticking.

If the user gave you full autonomy, **pick the archetype + preset yourself** from the table. Otherwise, **prompt the user** to pick.

## Step 1 — Archetype → example agent → DSL preset

| Archetype | Clone this example | DSL preset |
|---|---|---|
| Universe trend-follower | `condor`, `cheetah` | `let_winners_run` |
| Single-asset alpha hunter | `kodiak` (SOL), `grizzly` (BTC), `polar` (ETH), `wolverine` (HYPE) | `let_winners_run` |
| XYZ specialist | `dire` (oil), `kestrel` (macro) | `balanced` |
| Multi-asset whitelist | `bison`, `hedgehog` | `balanced` |
| Trader-follower / hot-streak | `jackal`, `spider`, `raptor` | `let_winners_run` |
| Striker / rank-jump | `roach`, `jaguar`, `orca` | `balanced` |
| Funding-rate fade | `pangolin`, `barracuda` | `mean_reversion` |
| Contrarian / crowding-unwind | `owl`, `dog`, `lemon` | `mean_reversion` |
| Volume engine / high-frequency | `turbine` | `scalp` |

Fetch the chosen example's producer for the full reference (one fetch, optional):
`https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<example>/scripts/<example>-producer.py`

## Step 2 — The producer (copy this skeleton)

```python
# scripts/<skill>-producer.py
import os, sys
from pathlib import Path

_sdk_candidates = [
    str(Path.home() / ".openclaw" / "skills" / "senpi-trading-runtime"),
    str(Path(os.environ.get("OPENCLAW_WORKSPACE", "/data/workspace")) / "skills" / "senpi-trading-runtime"),
]
_sdk_path = next((p for p in _sdk_candidates if (Path(p) / "senpi_runtime_helpers").is_dir()), _sdk_candidates[0])
if _sdk_path not in sys.path:
    sys.path.insert(0, _sdk_path)

from senpi_runtime_helpers import SenpiClient, scanner_lock, tick_cache, producer_daemon

WALLET = os.environ["<SKILL>_WALLET"]
SCANNER_NAME = "<scanner_name>"           # MUST match the external_scanner name in runtime.yaml
LOCK_NAME = f"<skill>-{WALLET[2:10]}"     # per-wallet — multi-wallet-host safe

client = SenpiClient()
mcp = tick_cache(client)                  # per-tick memoization; call MCP tools through this

def run_one_tick():
    with scanner_lock(LOCK_NAME):
        # 1. Pull whatever your thesis needs (cached per tick):
        markets = mcp("leaderboard_get_markets", limit=100)
        # ch = mcp("strategy_get_clearinghouse_state", strategy_wallet=WALLET)  # current positions
        # 2. Score / gate per your thesis. 3. Emit ONLY when a signal qualifies:
        if signal_ready:
            client.push_signal(
                address=WALLET, scanner=SCANNER_NAME,
                asset="BTC", direction="LONG",   # asset & direction are TOP-LEVEL (never inside data)
                score=0.85,                       # 0..1 confidence
                signal_type="<YOUR_TYPE>",
                data={"your_factor": 1.23},       # must match config.fields in runtime.yaml
            )

if __name__ == "__main__":
    producer_daemon(
        fn=run_one_tick,
        interval_seconds=300,                 # your tick cadence
        name=LOCK_NAME,
        wallet=WALLET,                        # daemon self-terminates if the runtime is
        scanner=SCANNER_NAME,                 # deleted OR the scanner is renamed
    )
```

The config/wrapper module (`scripts/<skill>_config.py`) just exposes the `SenpiClient`; copy it from the example agent — it's boilerplate.

## Step 3 — `runtime.yaml` (complete minimal template)

```yaml
version: 1                       # plugin SCHEMA version (always 1) — NOT your agent semver

strategy:
  wallet: "${WALLET_ADDRESS}"
  slots: 1                       # max concurrent positions
  margin_per_slot: 200           # USD per position (or use margin_pct)
  enabled: true

notifications:
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"

scanners:
  - name: position_tracker       # REQUIRED for exit management
    type: position_tracker
    interval: 10s
  - name: <scanner_name>         # MUST match SCANNER_NAME in the producer
    type: external_scanner       # push-driven — do NOT set interval
    outputs: { signals: true, context: false }
    config:
      fields:                    # every key your producer puts in `data`
        your_factor: { type: number, required: true }

actions:
  - name: <skill>_entry
    action_type: OPEN_POSITION
    decision_mode: llm           # llm gate; or `rule` for pass-through
    decision_model: ${<SKILL>_DECISION_MODEL}   # BARE model name, no provider prefix
    scanners: [<scanner_name>]
    min_confidence: 7
    params:
      order_type: FEE_OPTIMIZED_LIMIT
      fee_optimized_limit_options: { ensure_execution_as_taker: true, execution_timeout_seconds: 15 }
    context:
      - { type: signal, scanner: <scanner_name> }
    decision_prompt: |
      Decide whether to open this position. Approve only if the signal is clean.
      {{signal_<scanner_name>}}

exit:
  engine: dsl
  interval_seconds: 30
  dsl_preset:
    # PASTE the chosen preset's dsl_preset block from references/dsl-presets.yaml.
    # Default = balanced (shown). Swap per the Step-1 table.
    phase1: { enabled: true, max_loss_pct: 15.0, retrace_threshold: 10, consecutive_breaches_required: 1 }
    phase2:
      enabled: true
      tiers:
        - { trigger_pct: 10,  lock_hw_pct: 0 }
        - { trigger_pct: 20,  lock_hw_pct: 30 }
        - { trigger_pct: 35,  lock_hw_pct: 50 }
        - { trigger_pct: 60,  lock_hw_pct: 70 }
        - { trigger_pct: 100, lock_hw_pct: 85 }
    hard_timeout: { enabled: true, interval_in_minutes: 4320 }   # 72h outer bound
    weak_peak_cut: { enabled: true, interval_in_minutes: 360, min_value: 3.0 }

risk:
  guard_rails:
    daily_loss_limit_pct: 4
    max_entries_per_day: 6
    max_consecutive_losses: 3
    cooldown_minutes: 90
    drawdown_halt_pct: 20
    per_asset_cooldown_minutes: 45
```

## Step 4 — DSL presets (pick one; default `balanced`)

`balanced` is inlined in Step 3. For the others, copy the `dsl_preset` block from [`dsl-presets.yaml`](dsl-presets.yaml):

| Preset | Character |
|---|---|
| `balanced` ⭐ | Breathes early, runner tier to +100%, 72h outer bound, smart time-cuts |
| `let_winners_run` | Widest — no time-cuts; for trend / momentum / followers |
| `mean_reversion` | Tight — banks the snapback fast (lock 30% @ +5%); for faders |
| `scalp` | Tightest — fast locks + short timeouts; for high-frequency |

**Always attach DSL** unless the user explicitly opts out — it's what protects a position if the producer goes quiet or a fill lands late.

## Step 5 — Verify

```bash
ps -ef | grep <skill>-producer | grep -v grep        # exactly one process
senpi-helpers list                                    # TICKS ≥ 1, ERRORS = 0
grep daemon_tick_finished /tmp/<skill>-producer.log | tail -3   # "status":"ok"
```

---

## Gotchas (read before you ship — these cost roundtrips otherwise)

- **`asset` and `direction` are TOP-LEVEL `push_signal` args, never inside `data`.** Putting them in `data` makes the runtime store two copies and rejects with `INVALID_REQUEST`.
- **`decision_model` takes a BARE model name** (`gemini-2.5-pro`, `claude-sonnet-4-20250514`) — no provider prefix, or the gateway returns `500 Unknown model`.
- **`runtime.yaml` `version:` is always `1`** (plugin schema major). Your agent semver lives in SKILL.md frontmatter — NOT here.
- **Runtime package on main is `@senpi-ai/runtime`** (with the `-ai`), never `@senpi/runtime`.
- **Confirm the funding-rate cadence before annualizing.** The fleet currently disagrees — some producers annualize `×8760` (per-hour assumption), others `×3×365` (per-8h). Check Hyperliquid's published funding mechanics and annualize consistently; don't copy a number blindly. (Convention reconciliation pending.)
- **One runtime per wallet.** Installing a second for the same wallet is rejected — delete the first.

## Deep references (only if the template above isn't enough)

- [`producer-patterns.md`](producer-patterns.md) — full archetype catalog + example links
- [`python-producer-sdk.md`](python-producer-sdk.md) — full SDK (batch, parallel, cache, errors)
- [`yaml-schema.md`](yaml-schema.md) — every `runtime.yaml` field
- [`dsl-configuration.md`](dsl-configuration.md) — DSL field reference + tuning
- [`risk-gates.md`](risk-gates.md) — risk guard-rail semantics
- [`strategy-examples.md`](strategy-examples.md) — full worked examples
