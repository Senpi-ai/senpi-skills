/**
 * Notification service — Telegram integration.
 *
 * Ports: wolf_config.py → send_notification()
 * Silently fails — notifications should never crash the caller.
 */

import { SenpiMcpClient } from './mcp-client.js';
import { logger } from './logger.js';

export class NotificationService {
  private mcp: SenpiMcpClient;
  private telegramChatId: string | null = null;

  constructor(mcp: SenpiMcpClient) {
    this.mcp = mcp;
  }

  /** Configure the Telegram chat ID */
  setChatId(chatId: string): void {
    this.telegramChatId = chatId;
  }

  /** Send a notification. Silently fails — never crashes the caller. */
  async send(message: string): Promise<void> {
    if (!this.telegramChatId) {
      logger.debug('Notification skipped: no telegram chat ID configured');
      return;
    }

    try {
      const target = `telegram:${this.telegramChatId}`;
      await this.mcp.sendNotification(target, message);
    } catch (err) {
      logger.warn('Notification send failed', { error: String(err) });
    }
  }

  /** Send multiple notifications */
  async sendAll(messages: string[]): Promise<void> {
    for (const msg of messages) {
      await this.send(msg);
    }
  }
}
