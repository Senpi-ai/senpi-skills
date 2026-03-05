const DEFAULT_INTERVAL_MS = 3 * 60 * 1000;

/**
 * Parse cron to interval in ms. Other patterns use 3-minute default.
 */
function cronToMs(schedule: string): number {
  const trimmed = schedule.trim();
  const match = trimmed.match(/^\*\/(\d+)\s+\* \* \* \*$/);
  if (match) {
    const minutes = Math.max(1, parseInt(match[1], 10));
    return minutes * 60 * 1000;
  }
  return DEFAULT_INTERVAL_MS;
}

export type CronSchedulerOptions = {
  /** Called when a tick rejects (e.g. timeout, script error). */
  onTickError?: (strategyId: string, err: unknown) => void;
};

export class CronScheduler {
  private readonly onTickError?: (strategyId: string, err: unknown) => void;
  private readonly intervals = new Map<string, ReturnType<typeof setInterval>>();

  constructor(options: CronSchedulerOptions = {}) {
    this.onTickError = options.onTickError;
  }

  /**
   * Start a recurring tick for strategyId. Idempotent: no-op if already scheduled.
   */
  start(strategyId: string, tickFn: () => Promise<void>, schedule: string): void {
    if (this.intervals.has(strategyId)) {
      return;
    }
    const ms = cronToMs(schedule);
    const id = setInterval(() => {
      tickFn().catch((err) => {
        this.onTickError?.(strategyId, err);
      });
    }, ms);
    this.intervals.set(strategyId, id);
  }

  stop(strategyId: string): void {
    const id = this.intervals.get(strategyId);
    if (id !== undefined) {
      clearInterval(id);
      this.intervals.delete(strategyId);
    }
  }

  stopAll(): void {
    for (const id of this.intervals.values()) {
      clearInterval(id);
    }
    this.intervals.clear();
  }

  listActive(): string[] {
    return Array.from(this.intervals.keys());
  }
}
