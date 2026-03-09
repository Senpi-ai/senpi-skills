/**
 * Hook System — Typed event emitter + dispatch.
 *
 * New component (replaces cron-based architecture).
 * When a hook fires:
 * 1. Look up skill's hook config for this event type
 * 2. If decision_mode: none → execute built-in action only
 * 3. If decision_mode: llm → build context → call llm-task → parse response → execute
 * 4. If decision_mode: agent → construct event payload → wake agent (future)
 * 5. If notify: true → send Telegram notification
 */

import { HookConfig, HookEvent, HookEventType } from './types.js';
import { NotificationService } from './notifications.js';
import { logger } from './logger.js';

type HookHandler = (event: HookEvent) => Promise<void>;

export class HookSystem {
  private skillHooks = new Map<string, Record<string, HookConfig>>();
  private handlers = new Map<string, HookHandler[]>();
  private notifications: NotificationService;

  constructor(notifications: NotificationService) {
    this.notifications = notifications;
  }

  /** Register all hooks for a skill */
  registerSkillHooks(skillName: string, hooks: Record<string, HookConfig>): void {
    this.skillHooks.set(skillName, hooks);
    logger.info(`Registered ${Object.keys(hooks).length} hooks for skill: ${skillName}`);
  }

  /** Register an external handler for a hook event type */
  on(eventType: HookEventType, handler: HookHandler): void {
    const existing = this.handlers.get(eventType) ?? [];
    existing.push(handler);
    this.handlers.set(eventType, existing);
  }

  /** Fire a hook event */
  async fire(skillName: string, event: HookEvent): Promise<void> {
    logger.debug(`Hook fired: ${event.type}`, {
      skillName,
      strategyKey: event.strategyKey,
    });

    // Look up skill config for this event type
    const hooks = this.skillHooks.get(skillName);
    const hookConfig = hooks?.[event.type];

    // Run registered handlers
    const handlers = this.handlers.get(event.type) ?? [];
    for (const handler of handlers) {
      try {
        await handler(event);
      } catch (err) {
        logger.error(`Hook handler error for ${event.type}`, {
          error: String(err),
        });
      }
    }

    // Handle notification if configured
    if (hookConfig?.notify) {
      const message = this.formatNotification(event);
      await this.notifications.send(message);
    }

    // Handle decision_mode if configured
    if (hookConfig?.decision_mode === 'llm') {
      // LLM decision will be handled by the skill's entry handler
      // The hook system just dispatches — the LlmDecision layer handles the call
      logger.debug(`Hook ${event.type} requires LLM decision`);
    }

    if (hookConfig?.wake_agent) {
      logger.info(`Hook ${event.type} wants to wake agent (not yet implemented)`);
    }
  }

  /** Format a notification message from a hook event */
  private formatNotification(event: HookEvent): string {
    const prefix = event.strategyKey ? `[${event.strategyKey}]` : '';
    const data = event.data;

    switch (event.type) {
      case 'on_position_closed':
        return `${prefix} Position closed: ${data.asset} ${data.direction} — ${data.closeReason} (uPnL: $${data.upnl})`;
      case 'on_tier_changed':
        return `${prefix} Tier upgrade: ${data.asset} → Tier ${(data.newTier as number) + 1} (Phase ${data.phase})`;
      case 'on_daily_limit_hit':
        return `\u{1F6D1} ${prefix} Daily limit hit — scanning paused`;
      case 'on_drawdown_cap_hit':
        return `\u{1F6A8} ${prefix} Drawdown cap hit — emergency close triggered`;
      case 'on_consecutive_losses':
        return `\u{23F3} ${prefix} Consecutive losses — cooldown activated`;
      case 'on_position_at_risk':
        return `\u{26A0}\u{FE0F} ${prefix} Position at risk: ${data.msg}`;
      default:
        return `${prefix} ${event.type}: ${JSON.stringify(data)}`;
    }
  }
}
