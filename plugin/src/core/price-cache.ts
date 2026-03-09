/**
 * Price Cache — Shared price service.
 *
 * New component (currently duplicated across scripts as fetch_all_mids()).
 * Fetches market_get_prices every 5s for crypto, on-demand for XYZ.
 * In-memory Map<string, number> with staleness tracking.
 */

import { SenpiMcpClient } from './mcp-client.js';
import { logger } from './logger.js';

const FETCH_INTERVAL_MS = 5_000;
const STALE_WARNING_MS = 30_000;
const STALE_CRITICAL_MS = 60_000;

export class PriceCache {
  private prices = new Map<string, number>();
  private xyzPrices = new Map<string, number>();
  private lastFetchTime: number = 0;
  private lastXyzFetchTime: number = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private mcp: SenpiMcpClient;
  private fetching = false;

  constructor(mcp: SenpiMcpClient) {
    this.mcp = mcp;
  }

  /** Start automatic price fetching */
  start(): void {
    this.fetchPrices(); // Initial fetch
    this.timer = setInterval(() => this.fetchPrices(), FETCH_INTERVAL_MS);
    if (this.timer.unref) {
      this.timer.unref();
    }
  }

  /** Stop automatic price fetching */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /** Fetch all crypto prices */
  private async fetchPrices(): Promise<void> {
    if (this.fetching) return;
    this.fetching = true;
    try {
      const data = await this.mcp.getMarketPrices();
      for (const [asset, price] of Object.entries(data)) {
        if (!isNaN(price)) {
          this.prices.set(asset, price);
        }
      }
      this.lastFetchTime = Date.now();
    } catch (err) {
      logger.warn('Price fetch failed', { error: String(err) });
    } finally {
      this.fetching = false;
    }
  }

  /** Fetch XYZ prices on demand */
  async fetchXyzPrices(): Promise<void> {
    try {
      const data = await this.mcp.getMarketPrices({ dex: 'xyz' });
      for (const [asset, price] of Object.entries(data)) {
        if (!isNaN(price)) {
          this.xyzPrices.set(asset, price);
        }
      }
      this.lastXyzFetchTime = Date.now();
    } catch (err) {
      logger.warn('XYZ price fetch failed', { error: String(err) });
    }
  }

  /** Get price for an asset */
  getPrice(asset: string): number | null {
    // Check XYZ first
    if (asset.startsWith('xyz:')) {
      return this.xyzPrices.get(asset) ?? null;
    }
    // Check regular prices
    return this.prices.get(asset) ?? null;
  }

  /** Get all crypto prices */
  getAllPrices(): Record<string, number> {
    return Object.fromEntries(this.prices);
  }

  /** Get all XYZ prices */
  getAllXyzPrices(): Record<string, number> {
    return Object.fromEntries(this.xyzPrices);
  }

  /** How many ms since last successful fetch */
  getStalenessMs(): number {
    if (this.lastFetchTime === 0) return Infinity;
    return Date.now() - this.lastFetchTime;
  }

  /** Check if prices are stale beyond a threshold */
  isStale(thresholdMs = STALE_WARNING_MS): boolean {
    return this.getStalenessMs() > thresholdMs;
  }

  /** Check if prices are critically stale (DSL runner should skip) */
  isCriticallyStale(): boolean {
    return this.getStalenessMs() > STALE_CRITICAL_MS;
  }
}
