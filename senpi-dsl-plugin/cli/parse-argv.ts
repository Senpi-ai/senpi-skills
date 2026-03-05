/**
 * Parse argv (after "senpi-dsl" and subcommand) into a key-value map for known flags and positional args.
 * Example: ["add-dsl", "wolf", "--strategy-id", "x", "--asset", "ETH"] -> { preset: "wolf", strategyId: "x", asset: "ETH" }
 */
export function parseArgv(args: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  const positionals: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--strategy-id" || a === "--strategy_id") {
      out.strategyId = args[++i] ?? "";
    } else if (a === "--asset") {
      out.asset = args[++i] ?? "";
    } else if (a === "--direction") {
      out.direction = args[++i] ?? "";
    } else if (a === "--leverage") {
      out.leverage = args[++i] ?? "";
    } else if (a === "--margin") {
      out.margin = args[++i] ?? "";
    } else if (a === "--dex") {
      out.dex = args[++i] ?? "";
    } else if (a === "--config") {
      out.config = args[++i] ?? "";
    } else if (a === "--state-dir" || a === "--state_dir") {
      out.stateDir = args[++i] ?? "";
    } else if (!a.startsWith("--")) {
      positionals.push(a);
    }
  }
  if (positionals.length > 0) out.preset = positionals[0];
  return out;
}

export function getArgsAfterSubcommand(subcommand: string): string[] {
  const dslIdx = process.argv.indexOf("senpi-dsl");
  if (dslIdx < 0) return [];
  const idx = process.argv.indexOf(subcommand, dslIdx);
  return idx >= 0 ? process.argv.slice(idx + 1) : [];
}
