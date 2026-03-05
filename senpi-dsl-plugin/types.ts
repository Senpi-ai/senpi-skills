import { z } from "zod";
import { DEFAULT_STATE_DIR, DEFAULT_SCHEDULE } from "./constants.js";

// ---------------------------------------------------------------------------
// Plugin config
// ---------------------------------------------------------------------------

export const pluginConfigSchema = z.object({
  stateDir: z.string().default(DEFAULT_STATE_DIR),
  schedule: z.string().default(DEFAULT_SCHEDULE),
  alertChannelId: z.string().nullable().optional(),
});
export type PluginConfig = z.infer<typeof pluginConfigSchema>;

// ---------------------------------------------------------------------------
// Monitor NDJSON output (one line per position or strategy-level)
// ---------------------------------------------------------------------------

export type DslPositionOutput = {
  status: "ok" | "closed" | "breached" | "tier_changed" | "pending_close";
  asset: string;
  strategy_id: string;
  preset?: string;
  price?: number;
  floor?: number;
  tier?: number;
  close_result?: string;
  time: string;
  [key: string]: unknown;
};

export type DslStrategyInactiveOutput = {
  status: "strategy_inactive";
  strategy_id: string;
  [key: string]: unknown;
};

export type DslNoPositionsOutput = {
  status: "no_positions";
  strategy_id: string;
  [key: string]: unknown;
};

export type DslInactiveOutput = {
  status: "inactive";
  asset: string;
  strategy_id: string;
  time: string;
  [key: string]: unknown;
};

export type DslErrorOutput = {
  status: "error";
  error?: string;
  strategy_id?: string;
  [key: string]: unknown;
};

export type DslMonitorOutput =
  | DslPositionOutput
  | DslStrategyInactiveOutput
  | DslNoPositionsOutput
  | DslInactiveOutput
  | DslErrorOutput;

// ---------------------------------------------------------------------------
// CLI response (single JSON line from subcommands)
// ---------------------------------------------------------------------------

export type DslCliOutput = {
  action: string;
  status: "ok" | "error";
  strategy_id?: string;
  cron_needed?: boolean;
  cron_env?: Record<string, string>;
  cron_schedule?: string;
  [key: string]: unknown;
};
