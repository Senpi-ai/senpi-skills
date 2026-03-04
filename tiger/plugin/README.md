# Tiger OpenClaw Plugin

OpenClaw plugin that wraps Tiger's Python trading scripts as structured tool calls. Instead of the agent constructing shell commands and managing env vars, it calls tools like `tiger_dsl_tick` and `tiger_get_state` directly.

## Architecture

```
Agent ──tool call──▸ Plugin (TypeScript) ──spawn──▸ Python scripts
                         │
                         ├── PythonBridge    → spawns scripts, parses JSON stdout
                         ├── StateManager    → reads state files directly (no Python)
                         ├── ConfigResolver  → resolves workspace paths from plugin config
                         └── DslDefaults     → builds pattern-tuned DSL state
```

**Key principle**: Python scripts are untouched black boxes. The 255 Python tests remain authoritative. TypeScript tests cover only the bridge, registration, and state access layers.

### Source Layout

```
plugin/
├── package.json              # @senpi/tiger-plugin manifest
├── openclaw.plugin.json      # Plugin config schema + UI hints
├── tsconfig.json             # ES2022/Node16 strict
├── vitest.config.ts          # 30s timeout, V8 coverage
└── src/
    ├── index.ts              # Plugin entry: registers tools + service + lifecycle hook
    ├── types.ts              # All TypeScript types (mirrors Python state schemas)
    ├── python-bridge.ts      # Spawn Python scripts, collect JSON stdout
    ├── state-manager.ts      # Read/write state files in TypeScript
    ├── config-resolver.ts    # Resolve workspace + paths from plugin config
    ├── dsl-defaults.ts       # Pattern-specific DSL state builder
    ├── services/
    │   └── dsl-runner.ts     # Background service: periodic DSL ticks
    ├── tools/
    │   ├── index.ts          # Barrel export
    │   ├── dsl-tick.ts       # tiger_dsl_tick
    │   ├── get-state.ts      # tiger_get_state
    │   ├── get-dsl-state.ts  # tiger_get_dsl_state
    │   ├── get-trade-log.ts  # tiger_get_trade_log
    │   ├── create-dsl.ts     # tiger_create_dsl
    │   └── deactivate-dsl.ts # tiger_deactivate_dsl
    └── __tests__/            # Mirrors src/ structure
```

## Setup

**Requirements**: Node.js >= 22, Python 3 (for script execution), OpenClaw >= 2026.2.0

```bash
cd tiger/plugin
npm install
npm run build
```

### Plugin Configuration

In your OpenClaw `config.yaml`, add:

```yaml
plugins:
  tiger:
    workspace: /absolute/path/to/tiger/workspace
    # pythonPath: python3        # optional, default: python3
    # scriptTimeout: 55000       # optional, ms per script call
    # dslTickInterval: 30000     # optional, ms between DSL ticks
```

The workspace directory must contain `scripts/` (Python scripts) and `state/` (runtime state files).

Alternatively, set the `TIGER_WORKSPACE` environment variable. Plugin config takes precedence over the env var.

## Development

```bash
npm run dev          # watch mode — rebuilds on change
npm test             # run all tests
npm run test:coverage # with V8 coverage report
```

All tests mock I/O (child_process, fs) — no Python or workspace needed to run them. State manager tests use temp directories for real file I/O verification.

## Registered Tools

### tiger_dsl_tick

Runs the DSL v4 trailing stop processor.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | `"combined"` \| `"single"` | No | `combined` processes all assets (default), `single` targets one |
| `asset` | string | When mode=single | Asset symbol (e.g. `ETH`) |
| `strategy_id` | string | No | Uses config default if omitted |

Spawns `scripts/dsl-v4.py`. In single mode, sets `DSL_STATE_FILE` env var pointing to the specific asset's state file. Returns the script's JSON output directly.

### tiger_create_dsl

Creates a new DSL trailing stop state for a position.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `asset` | string | Yes | Asset symbol |
| `direction` | `"LONG"` \| `"SHORT"` | Yes | Position direction |
| `entry_price` | number | Yes | Entry price |
| `size` | number | Yes | Position size in USD |
| `leverage` | number | Yes | Leverage multiplier |
| `wallet` | string | Yes | Wallet address |
| `pattern` | string | Yes | Signal pattern (e.g. `COMPRESSION_BREAKOUT`) |
| `strategy_id` | string | No | Uses config default if omitted |
| `absolute_floor` | number | No | Custom floor price (default: entry ± 2%) |

Applies pattern-specific tuning from `dsl-defaults.ts`. Prevents duplicate states — errors if an active DSL already exists for the asset.

### tiger_deactivate_dsl

Deactivates DSL monitoring for an asset.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `asset` | string | Yes | Asset symbol |
| `reason` | string | Yes | Why it's being deactivated |
| `strategy_id` | string | No | Uses config default if omitted |

Idempotent — safe to call on already-inactive or missing states. Returns status: `deactivated`, `already_inactive`, or `not_found`.

### tiger_get_state

Returns the current strategy state (balances, positions, safety, aggression level).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `strategy_id` | string | No | Uses config default if omitted |

### tiger_get_dsl_state

Reads DSL trailing stop state.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `asset` | string | No | Specific asset, or omit for list mode |
| `strategy_id` | string | No | Uses config default if omitted |

With `asset`: returns full DslState. Without: returns summary of all active DSL states (asset, phase, tierIndex, direction, prices).

### tiger_get_trade_log

Returns recent trade log entries.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `strategy_id` | string | No | Uses config default if omitted |
| `limit` | number | No | Number of entries (default: 20, max: 500) |

## Background Service

### tiger-dsl-runner

Runs DSL ticks automatically on a timer (default: every 30s). Registered with OpenClaw's service lifecycle:

- **start()**: Begins the interval, runs first tick immediately
- **stop()**: Clears the timer

Has an overlap guard — if a tick is still running when the next interval fires, it skips instead of stacking.

## Core Modules

### PythonBridge (`src/python-bridge.ts`)

Spawns Python scripts and parses their JSON stdout.

```typescript
const result = await bridge.run<DslTickResult>('dsl-v4.py', {
  env: { DSL_STATE_FILE: '/path/to/dsl-ETH.json' },
  timeout: 30000,
});
```

- Sets `TIGER_WORKSPACE` env var on every call
- Uses `AbortController` for timeouts
- **Always attempts JSON.parse on stdout**, even on non-zero exit codes. DSL scripts exit 1 on errors but still produce valid JSON (see `dsl-v4.py` line 344-345).
- No retry logic — Python scripts handle their own retries internally

### StateManager (`src/state-manager.ts`)

Reads and writes state files directly in TypeScript. No Python spawn needed.

State files live in `{workspace}/state/{strategyId}/`:
- `tiger-state.json` — strategy runtime state
- `dsl-{ASSET}.json` — per-asset DSL trailing stop state
- `trade-log.json` — trade history array
- `tiger-config.json` — strategy configuration

**Writes are atomic**: writes to a `.tmp` file then renames. This prevents partial writes if the process crashes mid-write.

Missing or corrupted files return sensible defaults (empty state, empty arrays) instead of throwing.

### ConfigResolver (`src/config-resolver.ts`)

Resolution order:
1. Plugin config `workspace` field (from `config.yaml`)
2. `TIGER_WORKSPACE` env var
3. Error with clear message

Validates that the workspace directory exists and contains `scripts/dsl-v4.py`.

### DslDefaults (`src/dsl-defaults.ts`)

Builds initial DSL state with pattern-specific tuning:

| Pattern | Phase 1 Trail | Phase 2 Trail | Breach Decay |
|---------|---------------|---------------|--------------|
| COMPRESSION_BREAKOUT | 1.5% | 1.2% | soft |
| CORRELATION_LAG | 1.5% | 1.2% | soft |
| MOMENTUM_BREAKOUT | 1.2% | 1.0% | soft |
| MEAN_REVERSION | 1.5% | 1.2% | soft |
| FUNDING_ARB | 2.0% | 1.5% | soft |

Default absolute floor: entry price ± 2% (direction-dependent). 4 standard tiers with increasing profit targets (5%, 10%, 20%, 35%).

## Adding a New Tool

1. Create `src/tools/my-tool.ts` following existing tool patterns:
   - Define a TypeBox schema for input validation
   - Export a `createMyToolHandler(deps)` factory that receives `PythonBridge`, `StateManager`, and/or `ConfigResolver`
   - Return `{ handler, schema }` from the factory

2. Add the export to `src/tools/index.ts`

3. Register in `src/index.ts` inside `register(api)`:
   ```typescript
   api.registerTool('tiger_my_tool', {
     description: 'What the tool does',
     schema: myToolSchema,
     handler: createMyToolHandler({ bridge, stateManager, paths }),
   });
   ```

4. Add tests in `src/__tests__/tools/my-tool.test.ts` — mock dependencies, test schema validation and handler logic

5. Run `npm test` and `npm run build`

## Wrapping a New Python Script

To bridge a new Python script (e.g. `risk-guardian.py`):

1. Place the script in the workspace's `scripts/` directory
2. Create a tool that calls `bridge.run('risk-guardian.py', options)`
3. The script must write JSON to stdout — the bridge parses it automatically
4. Set any required env vars via the `options.env` parameter
5. Define the expected output type in `src/types.ts`

## Design Decisions

- **Wrap Python, don't rewrite**: 255 existing Python tests validate trading logic. The bridge adds ~50ms overhead per call, negligible for 5m-30s trading intervals.
- **State reads in TypeScript, writes are still in Python**: Reads are simple JSON parsing. Writes in Python have atomic semantics and race condition guards that are battle-tested (e.g., halt flag preservation in `save_state()`).
- **Exception**: `writeDslState` is in TypeScript because `create-dsl` and `deactivate-dsl` tools need to write DSL state without a Python roundtrip. Uses atomic tmp+rename.
- **No runtime dependencies**: Plugin uses Node.js stdlib only (`child_process`, `fs`, `path`). TypeBox is provided by the OpenClaw runtime.
- **Plugin config provides workspace**: Replaces env var management. The bridge sets `TIGER_WORKSPACE` in each child process env from plugin config.

---

## OpenClaw Integration Details

This section covers how the plugin maps to OpenClaw's plugin system. Read alongside the [OpenClaw Plugin Development Guide v2](../../../openclaw-plugin-development-guide-v2.md).

### Plugin Discovery

OpenClaw discovers this plugin via the `openclaw` field in `package.json`:

```json
{
  "openclaw": {
    "type": "extension",
    "extensions": ["./dist/index.js"]
  }
}
```

The plugin must be placed in one of OpenClaw's scanned locations:
- `~/.openclaw/extensions/` (global)
- `{workspaceDir}/.openclaw/extensions/` (workspace)
- A path listed in `plugins.loadPaths` in `config.yaml`

General `node_modules` is **not** scanned. Symlinks work but must pass realpath and ownership checks.

### What Gets Registered

| OpenClaw API | Count | Details |
|-------------|-------|---------|
| `api.registerTool()` | 6 | All trading tools (dsl-tick, get-state, etc.) |
| `api.registerService()` | 1 | `tiger-dsl-runner` background service |
| `api.on('gateway_start')` | 1 | Startup log message |

The plugin does **not** currently register: hooks for message lifecycle, HTTP endpoints, CLI subcommands, gateway RPC methods, or commands.

### Inline PluginApi Interface

The plugin defines its own `PluginApi` interface in `index.ts` rather than importing from `openclaw/plugin-sdk`. This avoids a compile-time dependency but means the interface can drift. If you add features that use newer API methods (e.g., `api.registerHttpRoute()`, `api.registerCommand()`, `api.runtime.state.resolveStateDir()`), you must extend this local interface to match.

### Config Validation Gap

The runtime `configSchema.parse()` in `index.ts` does minimal validation (just checks `typeof === 'object'`). The real validation happens inside `parsePluginConfig()` in `config-resolver.ts`. The `openclaw.plugin.json` schema is what the OpenClaw UI uses for rendering config fields. If you add new config fields, update **both** the JSON schema and the `parsePluginConfig` function.

### Available Hook Points for Extension

If building on this plugin, these OpenClaw hooks are relevant but not yet used:

| Hook | Use Case |
|------|----------|
| `before_tool_call` | Block tool calls based on conditions (e.g., halt state, market hours) |
| `after_tool_call` | Log tool results, trigger alerts on position closures |
| `gateway_stop` | Additional cleanup beyond the service `stop()` |
| `before_prompt_build` | Inject current portfolio state into agent context automatically |
| `message_received` | Auto-trigger DSL tick on certain inbound messages |

Example — blocking tools when the kill switch is active:

```typescript
api.on('before_tool_call', async (event) => {
  if (event.toolName.startsWith('tiger_') && event.toolName !== 'tiger_get_state') {
    const state = await stateManager.getState(defaultStrategyId);
    if (state?.halt) {
      return { blocked: true, reason: 'Tiger kill switch is active' };
    }
  }
}, { priority: 10 });
```

### Logging

The plugin uses `api.logger` directly. For more structured logging in sub-modules, use:

```typescript
const logger = api.runtime.logging.getChildLogger('tiger');
logger.info('DSL tick completed', { asset: 'ETH', phase: 2 });
```

---

## E2E Testing with OpenClaw

Unit tests (115 cases, all mocked) verify the TypeScript bridge, registration, and state layers. E2E tests verify the full chain: OpenClaw gateway → plugin → Python scripts → state files → agent response.

### Prerequisites for E2E

- OpenClaw installed and buildable (`pnpm build`)
- Python 3 with Tiger's dependencies installed
- A real Tiger workspace with `scripts/` and `state/` directories
- A configured LLM provider (at minimum, an API key for one provider)

### Running E2E tests manually

```bash
# 1. Enable the plugin in config
# ~/.openclaw/config.yaml:
#   plugins:
#     enabled: [tiger]
#     tiger:
#       workspace: /path/to/tiger/workspace

# 2. Start the gateway
pnpm openclaw gateway run

# 3. Test tool execution via the send command
pnpm openclaw send "Use the tiger_get_state tool"
pnpm openclaw send "Use the tiger_get_dsl_state tool"
pnpm openclaw send "Use the tiger_dsl_tick tool in combined mode"
pnpm openclaw send "Use the tiger_get_trade_log tool with limit 5"
```

### Writing automated E2E tests

E2E test files use the `*.e2e.test.ts` naming convention. They require a running gateway or spawn one in the test setup.

```typescript
// src/__tests__/e2e/tiger-tools.e2e.test.ts
import { describe, it, expect, beforeAll, afterAll } from 'vitest';

describe('tiger plugin e2e', () => {
  // Option A: Connect to an already-running gateway
  // Option B: Start a gateway process in beforeAll

  it('should return state via tiger_get_state', async () => {
    // Use the OpenClaw send CLI or WebSocket client to invoke the tool
    // Then assert on the response structure
  });

  it('should create and deactivate a DSL state', async () => {
    // 1. Call tiger_create_dsl with test parameters
    // 2. Verify the state file was created
    // 3. Call tiger_deactivate_dsl
    // 4. Verify state is marked inactive
  });
});
```

Run with the `LIVE` flag to distinguish from unit tests:

```bash
LIVE=1 pnpm test extensions/tiger-plugin -- --run e2e
```

### E2E via the WebSocket protocol

For programmatic E2E tests, connect directly to the gateway's WebSocket:

```typescript
import WebSocket from 'ws';

const ws = new WebSocket('ws://localhost:18789');
ws.on('open', () => {
  // Send connect frame (handshake)
  ws.send(JSON.stringify({ type: 'connect', token: '...' }));
});
ws.on('message', (data) => {
  const msg = JSON.parse(data.toString());
  // Handle typed response/event frames
});
```

### E2E test isolation tips

- Use a **separate workspace directory** with synthetic state files so tests don't affect real trading state
- Set `dslTickInterval` to a high value (e.g., `600000`) during E2E to prevent the background service from interfering
- Create a test-specific `config.yaml` and set `CONFIG_PATH` env var to point to it
- Use `pnpm openclaw gateway run --port 19999` to avoid conflicts with a production gateway

---

## Known Issues and Improvement Opportunities

### `@sinclair/typebox` dependency placement

TypeBox is in `devDependencies` but its `Type.*` objects are evaluated at runtime (passed to `api.registerTool()`). OpenClaw's runtime currently provides TypeBox via its Jiti loader, so this works in practice. If distributing the plugin standalone via npm, move `@sinclair/typebox` to `dependencies` or `peerDependencies`.

### `openclaw` not in `devDependencies`

The peer dependency is declared but never installed locally. The plugin hand-rolls its own `PluginApi` interface. For better type safety, add `"openclaw": "workspace:*"` (in-repo) or `"openclaw": ">=2026.2.0"` (standalone) to `devDependencies` and import `OpenClawPluginApi` from `openclaw/plugin-sdk`.

### Service ignores `ctx` argument

The `registerService` interface passes `ctx: { stateDir: string; logger: Logger; config: Config }` to `start()`/`stop()`, but both ignore it — the `DslRunner` already has everything it needs from the constructor. This is fine functionally but means the plugin uses its own resolved state directory rather than OpenClaw's managed `api.runtime.state.resolveStateDir()` path.

### No CLI subcommands

Unlike the SecureClaw plugin (which registers 10 CLI subcommands), Tiger has no CLI integration. Adding commands like `openclaw tiger status`, `openclaw tiger tick`, or `openclaw tiger halt` would be useful for operators:

```typescript
api.registerCli(({ program }) => {
  const cmd = program.command('tiger').description('Tiger trading tools');
  cmd.command('status').action(async () => {
    const state = await stateManager.getState(defaultStrategyId);
    console.log(JSON.stringify(state, null, 2));
  });
  cmd.command('tick').action(async () => {
    const result = await bridge.run('dsl-v4.py');
    console.log(JSON.stringify(result.data, null, 2));
  });
});
```

### No HTTP endpoint for external monitoring

Registering an HTTP route would allow external monitoring dashboards to query Tiger state:

```typescript
api.registerHttpRoute({
  path: '/tiger/status',
  handler: async (req, res) => {
    const state = await stateManager.getState(defaultStrategyId);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(state));
  },
});
```
