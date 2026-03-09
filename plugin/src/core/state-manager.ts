/**
 * State Manager — Atomic JSON state with in-memory cache.
 *
 * Ports: wolf_config.py → atomic_write(), registry loading, state paths
 *
 * Memory-first with event-driven disk flush (on state change + periodic 30s).
 * Atomic writes: write to .tmp, rename (same pattern as Python).
 * No file locks needed — in-process mutexes replace fcntl.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { DslState } from './types.js';
import { logger } from './logger.js';

export class StateManager {
  private cache = new Map<string, unknown>();
  private dirty = new Set<string>();
  private baseDir: string;
  private flushTimer: ReturnType<typeof setInterval> | null = null;

  constructor(baseDir: string) {
    this.baseDir = baseDir;
  }

  /** Start periodic flush (every 30s) */
  start(): void {
    this.flushTimer = setInterval(() => {
      this.flushAll().catch((err) =>
        logger.error('Periodic flush failed', { error: String(err) }),
      );
    }, 30_000);
    // Don't block process exit
    if (this.flushTimer.unref) {
      this.flushTimer.unref();
    }
  }

  /** Stop periodic flush */
  stop(): void {
    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }
  }

  /** Resolve a key to a file path */
  private keyToPath(key: string): string {
    return path.join(this.baseDir, `${key}.json`);
  }

  /** Read state (memory-first, disk fallback) */
  read<T>(key: string): T | null {
    if (this.cache.has(key)) {
      return this.cache.get(key) as T;
    }

    const filePath = this.keyToPath(key);
    try {
      const data = fs.readFileSync(filePath, 'utf-8');
      const parsed = JSON.parse(data) as T;
      this.cache.set(key, parsed);
      return parsed;
    } catch {
      return null;
    }
  }

  /** Write state (memory + mark for disk flush) */
  write<T>(key: string, data: T): void {
    this.cache.set(key, data);
    this.dirty.add(key);
  }

  /** Atomic disk persistence for a single key */
  async flush(key: string): Promise<void> {
    const data = this.cache.get(key);
    if (data === undefined) return;

    const filePath = this.keyToPath(key);
    const dir = path.dirname(filePath);
    await fs.promises.mkdir(dir, { recursive: true });

    const tmpPath = filePath + '.tmp';
    await fs.promises.writeFile(tmpPath, JSON.stringify(data, null, 2));
    await fs.promises.rename(tmpPath, filePath);
    this.dirty.delete(key);
  }

  /** Flush all dirty keys */
  async flushAll(): Promise<void> {
    const keys = [...this.dirty];
    await Promise.all(keys.map((key) => this.flush(key)));
  }

  /** Delete a key from cache and disk */
  async delete(key: string): Promise<void> {
    this.cache.delete(key);
    this.dirty.delete(key);
    const filePath = this.keyToPath(key);
    try {
      await fs.promises.unlink(filePath);
    } catch {
      // File may not exist
    }
  }

  // ─── DSL State Helpers ───

  /** Get the state directory for a skill+strategy */
  private dslDir(skillName: string, strategyKey: string): string {
    return path.join(this.baseDir, skillName, strategyKey);
  }

  /** Get DSL state for a specific position */
  getDslState(skillName: string, strategyKey: string, asset: string): DslState | null {
    const key = `${skillName}/${strategyKey}/dsl-${asset}`;
    return this.read<DslState>(key);
  }

  /** Set DSL state for a specific position */
  setDslState(skillName: string, strategyKey: string, asset: string, state: DslState): void {
    const key = `${skillName}/${strategyKey}/dsl-${asset}`;
    this.write(key, state);
  }

  /** List all active DSL states for a skill (optionally filtered by strategy) */
  listActiveDslStates(
    skillName: string,
    strategyKey?: string,
  ): { asset: string; strategyKey: string; state: DslState }[] {
    const results: { asset: string; strategyKey: string; state: DslState }[] = [];
    const skillDir = path.join(this.baseDir, skillName);

    if (!fs.existsSync(skillDir)) return results;

    const strategies = strategyKey
      ? [strategyKey]
      : fs.readdirSync(skillDir).filter((f) => {
          const fullPath = path.join(skillDir, f);
          return fs.statSync(fullPath).isDirectory();
        });

    for (const sk of strategies) {
      const dir = path.join(skillDir, sk);
      if (!fs.existsSync(dir)) continue;

      const files = fs.readdirSync(dir).filter(
        (f) => f.startsWith('dsl-') && f.endsWith('.json'),
      );

      for (const file of files) {
        const asset = file.replace('dsl-', '').replace('.json', '');
        const state = this.getDslState(skillName, sk, asset);
        if (state && state.active) {
          results.push({ asset, strategyKey: sk, state });
        }
      }
    }

    return results;
  }

  /** Atomic write a file directly (for compatibility with existing state files) */
  async atomicWriteFile(filePath: string, data: unknown): Promise<void> {
    const dir = path.dirname(filePath);
    await fs.promises.mkdir(dir, { recursive: true });
    const tmpPath = filePath + '.tmp';
    await fs.promises.writeFile(tmpPath, JSON.stringify(data, null, 2));
    await fs.promises.rename(tmpPath, filePath);
  }

  /** Read a file directly (for compatibility) */
  readFile<T>(filePath: string): T | null {
    try {
      const data = fs.readFileSync(filePath, 'utf-8');
      return JSON.parse(data) as T;
    } catch {
      return null;
    }
  }
}
