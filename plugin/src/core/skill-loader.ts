/**
 * Skill Loader — skill.yaml parser, validator, variable resolution.
 *
 * Parses YAML, validates against schema, resolves template variables.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import YAML from 'js-yaml';
import { z } from 'zod';
import { SkillYamlConfig } from './types.js';
import { logger } from './logger.js';

// ─── Zod Schema for skill.yaml ───

const DslPresetSchema = z.object({
  tiers: z.array(
    z.object({
      trigger_pct: z.number(),
      lock_pct: z.number(),
      breaches: z.number(),
    }),
  ),
  max_loss_pct: z.number(),
  stagnation: z.object({
    enabled: z.boolean(),
    min_roe: z.number(),
    timeout_minutes: z.number(),
  }),
});

const SkillYamlSchema = z.object({
  name: z.string(),
  version: z.string(),
  description: z.string().optional(),
  strategies: z.record(
    z.object({
      wallet: z.string(),
      strategy_id: z.string(),
      budget: z.union([z.string(), z.number()]),
      slots: z.number(),
      trading_risk: z.enum(['conservative', 'moderate', 'aggressive']),
      dsl_preset: z.string(),
    }),
  ),
  scanner: z.object({
    type: z.string(),
    interval: z.string(),
    config: z.record(z.unknown()).optional(),
    blocked_assets: z.array(z.string()).optional(),
  }),
  entry: z.object({
    decision_mode: z.enum(['llm', 'agent', 'none']),
    decision_model: z.string().optional(),
    decision_prompt: z.string(),
    context: z.array(z.string()),
    min_confidence: z.number(),
  }),
  exit: z.object({
    engine: z.string(),
    dsl_presets: z.record(DslPresetSchema),
    sm_flip: z
      .object({
        enabled: z.boolean(),
        interval: z.string(),
        conviction_collapse: z
          .object({
            from_min: z.number(),
            to_max: z.number(),
            window_minutes: z.number(),
            action: z.string(),
          })
          .optional(),
        dead_weight: z.object({ conviction_zero_action: z.string() }).optional(),
      })
      .optional(),
  }),
  risk: z.object({
    per_strategy: z.object({
      daily_loss_limit_pct: z.number(),
      margin_buffer_pct: z.number(),
      auto_delever: z
        .object({
          enabled: z.boolean(),
          threshold_pct: z.number(),
          reduce_to: z.number(),
        })
        .optional(),
    }),
    guard_rails: z.object({
      max_entries_per_day: z.number(),
      bypass_on_profit: z.boolean(),
      max_consecutive_losses: z.number(),
      cooldown_minutes: z.number(),
    }),
    leverage: z
      .object({
        aggressive_cap_pct: z.number().optional(),
        moderate_cap_pct: z.number().optional(),
        conservative_cap_pct: z.number().optional(),
      })
      .optional(),
    directional_guard: z.object({ max_same_direction: z.number() }).optional(),
    rotation_cooldown_minutes: z.number().optional(),
  }),
  hooks: z.record(z.any()),
  notifications: z.object({
    telegram_chat_id: z.string(),
  }),
});

/** Parse an interval string like "3m", "5m", "1h" to milliseconds */
export function parseInterval(interval: string): number {
  const match = interval.match(/^(\d+)(s|m|h)$/);
  if (!match) {
    throw new Error(`Invalid interval format: ${interval}. Use Ns, Nm, or Nh.`);
  }
  const value = parseInt(match[1], 10);
  const unit = match[2];
  switch (unit) {
    case 's':
      return value * 1000;
    case 'm':
      return value * 60_000;
    case 'h':
      return value * 3_600_000;
    default:
      throw new Error(`Unknown interval unit: ${unit}`);
  }
}

/** Resolve template variables like ${WALLET_1} from env or provided values */
function resolveVariables(
  raw: string,
  variables: Record<string, string> = {},
): string {
  return raw.replace(/\$\{(\w+)\}/g, (match, name) => {
    const value = variables[name] ?? process.env[name];
    if (value === undefined) {
      throw new Error(`Unresolved variable: ${match}. Set env var ${name} or provide it.`);
    }
    return value;
  });
}

/** Recursively resolve template variables in an object */
function resolveDeep(
  obj: unknown,
  variables: Record<string, string>,
): unknown {
  if (typeof obj === 'string') {
    return resolveVariables(obj, variables);
  }
  if (Array.isArray(obj)) {
    return obj.map((item) => resolveDeep(item, variables));
  }
  if (obj !== null && typeof obj === 'object') {
    const result: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj)) {
      result[key] = resolveDeep(value, variables);
    }
    return result;
  }
  return obj;
}

/**
 * Load and validate a skill.yaml file.
 *
 * @param yamlPath - Path to the skill.yaml file
 * @param variables - Optional variable overrides (otherwise uses env vars)
 * @returns Validated and resolved SkillYamlConfig
 */
export function loadSkillYaml(
  yamlPath: string,
  variables: Record<string, string> = {},
): SkillYamlConfig {
  const raw = fs.readFileSync(yamlPath, 'utf-8');
  const parsed = YAML.load(raw);

  // Resolve template variables
  const resolved = resolveDeep(parsed, variables);

  // Validate against schema
  const result = SkillYamlSchema.safeParse(resolved);
  if (!result.success) {
    const errors = result.error.issues
      .map((i) => `  ${i.path.join('.')}: ${i.message}`)
      .join('\n');
    throw new Error(`Invalid skill.yaml at ${yamlPath}:\n${errors}`);
  }

  logger.info(`Loaded skill: ${result.data.name} v${result.data.version}`);
  return result.data as SkillYamlConfig;
}

/**
 * Save a resolved skill config for restart recovery.
 */
export function saveResolvedConfig(
  configDir: string,
  skillName: string,
  config: SkillYamlConfig,
): void {
  const filePath = path.join(configDir, `skill-config-${skillName}.json`);
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(config, null, 2));
}

/**
 * Load a previously saved resolved config.
 */
export function loadResolvedConfig(
  configDir: string,
  skillName: string,
): SkillYamlConfig | null {
  const filePath = path.join(configDir, `skill-config-${skillName}.json`);
  try {
    const data = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(data) as SkillYamlConfig;
  } catch {
    return null;
  }
}
