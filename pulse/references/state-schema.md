# Pulse Heat State Schema

The Pulse scanner maintains a persistent heat state file that tracks the adaptive polling behavior.

## File Location

- **Default**: `{state_dir}/pulse-heat.json`
- **Instance-specific**: `{state_dir}/pulse-heat-{instance_id}.json`

## Schema Definition

```json
{
  "level": "cold|warm|hot",
  "consecutiveEmpty": 0,
  "lastEscalation": "2026-03-04T07:31:19Z",
  "updatedAt": "2026-03-04T08:33:42Z"
}
```

## Field Definitions

### `level` (string, required)
Current heat level of the scanner.

**Values**:
- `"cold"`: Baseline monitoring, 5-minute intervals
- `"warm"`: Elevated activity, 3-minute intervals  
- `"hot"`: High signal density, 90-second intervals

### `consecutiveEmpty` (integer, required)
Number of consecutive scans with zero qualifying signals.

**Behavior**:
- Increments when no signals found
- Resets to 0 when signals detected
- Triggers decay when ≥ `decay_threshold`

**Range**: `0` to `∞`

### `lastEscalation` (string, nullable)
ISO timestamp of the last action escalation event.

**Format**: ISO 8601 with Z suffix (UTC)
**Example**: `"2026-03-04T07:31:19Z"`
**Null**: No escalation has occurred yet

**Used for**:
- Hot persistence calculations
- Escalation frequency tracking
- Debugging timing issues

### `updatedAt` (string, nullable)
ISO timestamp of the last state update.

**Format**: ISO 8601 with Z suffix (UTC)
**Example**: `"2026-03-04T08:33:42Z"`
**Null**: State never updated (new installation)

**Used for**:
- State freshness validation
- Debugging update cycles
- Audit trail

## State Transitions

```
Initial State:
{
  "level": "cold",
  "consecutiveEmpty": 0,
  "lastEscalation": null,
  "updatedAt": null
}
```

### COLD → WARM
```json
// Before
{"level": "cold", "consecutiveEmpty": 0}

// After (signals ≥ warm_threshold)
{"level": "warm", "consecutiveEmpty": 0, "updatedAt": "..."}
```

### WARM → HOT  
```json
// Before
{"level": "warm", "consecutiveEmpty": 0}

// After (signals ≥ hot_threshold OR action=escalate)
{
  "level": "hot", 
  "consecutiveEmpty": 0,
  "lastEscalation": "2026-03-04T07:31:19Z",
  "updatedAt": "2026-03-04T07:31:19Z"
}
```

### HOT → WARM (Persistence Decay)
```json
// Before (hot_persistence cycles elapsed)
{
  "level": "hot",
  "lastEscalation": "2026-03-04T07:31:19Z",
  "updatedAt": "2026-03-04T08:01:19Z"  // 30 min later
}

// After
{
  "level": "warm",
  "lastEscalation": "2026-03-04T07:31:19Z",  // Preserved
  "updatedAt": "2026-03-04T08:01:19Z"
}
```

### WARM/HOT → COLD (Empty Decay)
```json
// Before (consecutiveEmpty ≥ decay_threshold)
{
  "level": "warm",
  "consecutiveEmpty": 3,
  "lastEscalation": "2026-03-04T07:31:19Z"
}

// After
{
  "level": "cold", 
  "consecutiveEmpty": 4,  // Still increments
  "lastEscalation": "2026-03-04T07:31:19Z",  // Preserved
  "updatedAt": "..."
}
```

## Validation Rules

1. **Level**: Must be one of `["cold", "warm", "hot"]`
2. **consecutiveEmpty**: Must be non-negative integer
3. **lastEscalation**: Must be valid ISO 8601 timestamp or null
4. **updatedAt**: Must be valid ISO 8601 timestamp or null
5. **Timestamps**: UTC timezone (Z suffix) required
6. **File atomicity**: Updates use temp file + rename pattern

## Error Handling

### Missing File
If state file doesn't exist, scanner creates default:
```json
{
  "level": "cold",
  "consecutiveEmpty": 0,
  "lastEscalation": null,
  "updatedAt": null
}
```

### Corrupted File
If JSON is invalid, scanner logs error and exits with status 1.
**Recovery**: Delete state file to reset to defaults.

### Invalid Schema
If required fields are missing or invalid types, scanner logs warning and uses defaults for missing fields.

## Multi-Instance Support

Each instance gets its own state file:

- `pulse-heat.json` (default instance)
- `pulse-heat-alpha.json` (instance_id="alpha")
- `pulse-heat-beta.json` (instance_id="beta")

This allows multiple scanners to run independently with isolated state tracking.

## Debugging

### State Inspection
```bash
# View current state
cat /data/workspace/state/pulse/pulse-heat.json | jq .

# Pretty print with timestamps
jq -r '"Level: \(.level), Empty: \(.consecutiveEmpty), Updated: \(.updatedAt)"' < pulse-heat.json
```

### State Reset
```bash
# Reset to cold state
rm /data/workspace/state/pulse/pulse-heat.json

# Next scanner run will recreate defaults
```

### Manual State Edit
```bash
# Temporarily force HOT state for testing
echo '{"level":"hot","consecutiveEmpty":0,"lastEscalation":"2026-03-04T08:00:00Z","updatedAt":"2026-03-04T08:00:00Z"}' > pulse-heat.json
```