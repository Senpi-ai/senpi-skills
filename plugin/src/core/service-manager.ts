/**
 * Service Manager — Background timer lifecycle.
 *
 * New component (replaces OpenClaw crons).
 * Each service gets a setInterval timer with overlap prevention,
 * error isolation, and health tracking.
 */

import { BackgroundService, ServiceHealth } from './types.js';
import { logger } from './logger.js';

interface ServiceEntry {
  service: BackgroundService;
  timer: ReturnType<typeof setInterval> | null;
  isRunning: boolean;
  lastTickTime: string | null;
  lastTickDurationMs: number;
  consecutiveFailures: number;
}

export class ServiceManager {
  private services = new Map<string, ServiceEntry>();

  /** Register a background service */
  register(service: BackgroundService): void {
    if (this.services.has(service.name)) {
      logger.warn(`Service ${service.name} already registered, replacing`);
      this.stopService(service.name);
    }

    this.services.set(service.name, {
      service,
      timer: null,
      isRunning: false,
      lastTickTime: null,
      lastTickDurationMs: 0,
      consecutiveFailures: 0,
    });
  }

  /** Start all registered services */
  startAll(): void {
    for (const [name, entry] of this.services) {
      this.startService(name, entry);
    }
  }

  /** Stop all services */
  stopAll(): void {
    for (const name of this.services.keys()) {
      this.stopService(name);
    }
  }

  /** Start a single service */
  private startService(name: string, entry: ServiceEntry): void {
    if (entry.timer) return;

    logger.info(`Starting service: ${name} (interval: ${entry.service.intervalMs}ms)`);

    // Run immediately, then on interval
    this.runTick(name, entry);

    entry.timer = setInterval(() => this.runTick(name, entry), entry.service.intervalMs);
    if (entry.timer.unref) {
      entry.timer.unref();
    }
  }

  /** Stop a single service */
  private stopService(name: string): void {
    const entry = this.services.get(name);
    if (entry?.timer) {
      clearInterval(entry.timer);
      entry.timer = null;
    }
  }

  /** Run a single tick with overlap prevention and error isolation */
  private async runTick(name: string, entry: ServiceEntry): Promise<void> {
    // Overlap prevention
    if (entry.isRunning) {
      logger.warn(`Service ${name} tick skipped — previous tick still running`);
      return;
    }

    entry.isRunning = true;
    const start = Date.now();

    try {
      await entry.service.tick();
      entry.consecutiveFailures = 0;
      entry.lastTickTime = new Date().toISOString();
      entry.lastTickDurationMs = Date.now() - start;
    } catch (err) {
      entry.consecutiveFailures += 1;
      logger.error(`Service ${name} tick failed`, {
        error: String(err),
        consecutiveFailures: entry.consecutiveFailures,
      });
    } finally {
      entry.isRunning = false;
    }
  }

  /** Get health status for all services */
  getHealth(): ServiceHealth[] {
    const results: ServiceHealth[] = [];
    for (const [, entry] of this.services) {
      results.push({
        name: entry.service.name,
        lastTickTime: entry.lastTickTime,
        lastTickDurationMs: entry.lastTickDurationMs,
        consecutiveFailures: entry.consecutiveFailures,
        isRunning: entry.isRunning,
      });
    }
    return results;
  }
}
