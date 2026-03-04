import type { PythonBridge } from '../python-bridge.js';
import type { TigerPluginConfig, DslTickResult } from '../types.js';

export interface DslRunnerLogger {
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

export class DslRunner {
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;

  constructor(
    private readonly bridge: PythonBridge,
    private readonly config: TigerPluginConfig,
    private readonly logger: DslRunnerLogger,
  ) {}

  start(): void {
    if (this.timer) return;

    this.logger.info(
      `[Tiger] DSL runner started — tick every ${this.config.dslTickInterval}ms`,
    );

    this.timer = setInterval(() => {
      void this.tick();
    }, this.config.dslTickInterval);

    // Run first tick immediately
    void this.tick();
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      this.logger.info('[Tiger] DSL runner stopped');
    }
  }

  get isRunning(): boolean {
    return this.timer !== null;
  }

  /** Exposed for testing and manual invocation via tiger_dsl_tick tool */
  async tick(): Promise<DslTickResult | null> {
    // Guard against overlapping ticks
    if (this.running) return null;
    this.running = true;

    try {
      const result = await this.bridge.run<DslTickResult>('dsl-v4.py');

      if (!result.success) {
        this.logger.error(
          `[Tiger] DSL tick failed (exit ${result.exitCode}): ${result.error ?? 'unknown error'}`,
        );
        return result.data ?? null;
      }

      // Log closures — any result entry with a close action
      if (result.data?.results) {
        for (const entry of result.data.results) {
          if (entry['closed'] || entry['action'] === 'CLOSE') {
            this.logger.warn(
              `[Tiger] DSL closed position: ${JSON.stringify(entry)}`,
            );
          }
        }
      }

      return result.data ?? null;
    } catch (err) {
      this.logger.error(
        `[Tiger] DSL tick exception: ${(err as Error).message}`,
      );
      return null;
    } finally {
      this.running = false;
    }
  }
}
