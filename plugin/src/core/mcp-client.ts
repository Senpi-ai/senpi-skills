/**
 * MCP Client — Typed Senpi MCP wrappers with retry.
 *
 * Ports: wolf_config.py → mcporter_call() / mcporter_call_safe()
 *
 * Provides typed request/response wrappers around raw MCP tool calls.
 * Uses subprocess calls to mcporter CLI.
 *
 * Every Senpi MCP tool used by the plugin has a typed wrapper here.
 * Skill attribution (skill_name, skill_version) injected automatically.
 */

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import {
  ClearinghouseState,
  CreateOrderParams,
  CreatePositionResult,
  ClosePositionResult,
  CloseOrderType,
  EditPositionParams,
  EditPositionResult,
  OpenOrder,
  TradingLimits,
  OrderStatus,
  InstrumentInfo,
  MarketAssetData,
  ClosedPosition,
  PortfolioData,
  OpenPositionDetails,
  LeaderboardMarket,
} from './types.js';
import { logger } from './logger.js';

const execFileAsync = promisify(execFile);

const DEFAULT_SKILL_NAME = 'wolf-strategy';
const DEFAULT_SKILL_VERSION = '7.0.0';

export interface McpCallOptions {
  retries?: number;
  timeoutMs?: number;
}

export interface SenpiMcpClientConfig {
  mcporterBin?: string;
  skillName?: string;
  skillVersion?: string;
}

export class SenpiMcpClient {
  private mcporterBin: string;
  private skillName: string;
  private skillVersion: string;

  constructor(config: SenpiMcpClientConfig = {}) {
    this.mcporterBin = config.mcporterBin || process.env.MCPORTER_CMD || 'mcporter';
    this.skillName = config.skillName || DEFAULT_SKILL_NAME;
    this.skillVersion = config.skillVersion || DEFAULT_SKILL_VERSION;
  }

  // ════════════════════════════════════════════════════════════════════
  // Raw call infrastructure
  // ════════════════════════════════════════════════════════════════════

  /** Raw MCP tool call with retry and exponential backoff */
  async callTool<T = unknown>(
    tool: string,
    args: Record<string, unknown> = {},
    opts: McpCallOptions = {},
  ): Promise<T> {
    const retries = opts.retries ?? 3;
    const timeoutMs = opts.timeoutMs ?? 30_000;

    // Inject skill attribution
    const filteredArgs: Record<string, unknown> = {
      skill_name: this.skillName,
      skill_version: this.skillVersion,
      ...args,
    };

    // Remove undefined/null values
    for (const key of Object.keys(filteredArgs)) {
      if (filteredArgs[key] === undefined || filteredArgs[key] === null) {
        delete filteredArgs[key];
      }
    }

    let lastError: unknown;

    for (let attempt = 0; attempt < retries; attempt++) {
      const tmpFile = path.join(
        os.tmpdir(),
        `mcp-${Date.now()}-${Math.random().toString(36).slice(2)}.json`,
      );
      try {
        const cmdArgs = ['call', `senpi.${tool}`];
        if (Object.keys(filteredArgs).length > 0) {
          cmdArgs.push('--args', JSON.stringify(filteredArgs));
        }

        const { stdout } = await execFileAsync(this.mcporterBin, cmdArgs, {
          timeout: timeoutMs,
          maxBuffer: 10 * 1024 * 1024,
        });

        await fs.promises.writeFile(tmpFile, stdout);
        const raw = await fs.promises.readFile(tmpFile, 'utf-8');
        const parsed = JSON.parse(raw);

        if (parsed.success) {
          return (parsed.data ?? {}) as T;
        }

        lastError = parsed.error || parsed;
      } catch (err) {
        lastError = err;
      } finally {
        try {
          await fs.promises.unlink(tmpFile);
        } catch {
          // Ignore cleanup errors
        }
      }

      if (attempt < retries - 1) {
        const delay = Math.pow(3, attempt) * 1000; // 1s, 3s, 9s
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }

    throw new Error(`MCP ${tool} failed after ${retries} attempts: ${String(lastError)}`);
  }

  /** Safe variant — returns null instead of throwing */
  async callToolSafe<T = unknown>(
    tool: string,
    args: Record<string, unknown> = {},
    opts: McpCallOptions = {},
  ): Promise<T | null> {
    try {
      return await this.callTool<T>(tool, args, opts);
    } catch (err) {
      logger.warn(`MCP ${tool} failed (safe)`, { error: String(err) });
      return null;
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // Market Data
  // ════════════════════════════════════════════════════════════════════

  /**
   * Fetch mid prices for all (or specific) assets.
   * Returns Map<asset, price> as numbers.
   */
  async getMarketPrices(opts?: {
    dex?: string;
    assets?: string[];
  }): Promise<Record<string, number>> {
    const args: Record<string, unknown> = {};
    if (opts?.dex) args.dex = opts.dex;
    if (opts?.assets) args.assets = opts.assets;

    const data = await this.callToolSafe<Record<string, unknown>>(
      'market_get_prices',
      args,
    );
    if (!data) return {};

    // Response may be { prices: { BTC: "105000.5", ... } } or flat
    const priceObj = (data.prices ?? data) as Record<string, string | number>;
    const result: Record<string, number> = {};
    for (const [asset, val] of Object.entries(priceObj)) {
      const num = typeof val === 'number' ? val : parseFloat(val);
      if (!isNaN(num)) result[asset] = num;
    }
    return result;
  }

  /**
   * Get comprehensive market data for a single asset:
   * candles, order book, funding history, and asset context.
   */
  async getMarketAssetData(
    asset: string,
    opts?: {
      dex?: string;
      candleIntervals?: string[];
      includeOrderBook?: boolean;
      includeFunding?: boolean;
    },
  ): Promise<MarketAssetData | null> {
    const args: Record<string, unknown> = { asset };
    if (opts?.dex) args.dex = opts.dex;
    if (opts?.candleIntervals) args.candle_intervals = opts.candleIntervals;
    if (opts?.includeOrderBook !== undefined) args.include_order_book = opts.includeOrderBook;
    if (opts?.includeFunding !== undefined) args.include_funding = opts.includeFunding;

    return this.callToolSafe<MarketAssetData>('market_get_asset_data', args);
  }

  /**
   * List all available perpetual instruments with metadata.
   * Returns instrument info including name, maxLeverage, szDecimals, etc.
   */
  async listInstruments(dex?: string): Promise<InstrumentInfo[]> {
    const args: Record<string, unknown> = {};
    if (dex) args.dex = dex;
    const data = await this.callToolSafe<{ instruments: InstrumentInfo[] }>(
      'market_list_instruments',
      args,
    );
    return data?.instruments ?? [];
  }

  // ════════════════════════════════════════════════════════════════════
  // Hyperfeed / Leaderboard (SM signal data)
  // ════════════════════════════════════════════════════════════════════

  /**
   * Get market concentration data — which assets are attracting
   * the most activity from top leaderboard traders (4h rolling window).
   */
  async getLeaderboardMarkets(limit = 100): Promise<LeaderboardMarket[]> {
    const data = await this.callTool<{ markets: LeaderboardMarket[] }>(
      'leaderboard_get_markets',
      { limit },
    );
    return data?.markets ?? [];
  }

  // ════════════════════════════════════════════════════════════════════
  // Account / Portfolio
  // ════════════════════════════════════════════════════════════════════

  /**
   * Get the user's complete portfolio: balances, positions, PnL, strategies.
   */
  async getPortfolio(opts?: {
    forceFetch?: boolean;
    strategyStatus?: 'ACTIVE' | 'ALL';
  }): Promise<PortfolioData | null> {
    const args: Record<string, unknown> = {};
    if (opts?.forceFetch) args.forceFetch = true;
    if (opts?.strategyStatus) args.strategyStatus = opts.strategyStatus;

    return this.callToolSafe<PortfolioData>('account_get_portfolio', args);
  }

  // ════════════════════════════════════════════════════════════════════
  // Strategy State
  // ════════════════════════════════════════════════════════════════════

  /**
   * Get full clearinghouse state for a wallet (both main + xyz DEX).
   * Positions, margin summary, withdrawable balance.
   */
  async getClearinghouseState(wallet: string): Promise<ClearinghouseState | null> {
    return this.callToolSafe<ClearinghouseState>(
      'strategy_get_clearinghouse_state',
      { strategy_wallet: wallet },
    );
  }

  /**
   * Get open/resting orders for a wallet.
   */
  async getOpenOrders(wallet: string, dex?: string): Promise<OpenOrder[]> {
    const args: Record<string, unknown> = { strategy_wallet: wallet };
    if (dex) args.dex = dex;
    const data = await this.callToolSafe<{ orders: OpenOrder[] }>(
      'strategy_get_open_orders',
      args,
    );
    return data?.orders ?? [];
  }

  /**
   * Get trading limits for a specific asset on a wallet:
   * max leverage, max size, available to trade, mark price.
   */
  async getAssetTradingLimits(
    wallet: string,
    coin: string,
    dex?: string,
  ): Promise<TradingLimits | null> {
    const args: Record<string, unknown> = { strategy_wallet: wallet, coin };
    if (dex) args.dex = dex;
    return this.callToolSafe<TradingLimits>(
      'strategy_get_asset_trading_limits',
      args,
    );
  }

  // ════════════════════════════════════════════════════════════════════
  // Order Management — create, close, edit, cancel
  // ════════════════════════════════════════════════════════════════════

  /**
   * Open one or more new positions.
   * Each order opens a new position for a distinct coin.
   */
  async createPosition(
    wallet: string,
    orders: CreateOrderParams[],
    opts?: McpCallOptions & { reason?: string },
  ): Promise<CreatePositionResult> {
    const mcpOrders = orders.map((o) => {
      const order: Record<string, unknown> = {
        coin: o.coin,
        direction: o.direction,
        leverage: Math.round(o.leverage),
        marginAmount: o.marginAmount,
        orderType: o.orderType,
      };
      if (o.leverageType) order.leverageType = o.leverageType;
      if (o.limitPrice !== undefined) order.limitPrice = o.limitPrice;
      if (o.timeInForce) order.timeInForce = o.timeInForce;
      if (o.slippagePercent !== undefined) order.slippagePercent = o.slippagePercent;
      if (o.ensureExecutionAsTaker !== undefined) order.ensureExecutionAsTaker = o.ensureExecutionAsTaker;
      if (o.takeProfit) order.takeProfit = o.takeProfit;
      if (o.stopLoss) order.stopLoss = o.stopLoss;
      return order;
    });

    const args: Record<string, unknown> = {
      strategyWalletAddress: wallet,
      orders: mcpOrders,
    };
    if (opts?.reason) args.reason = opts.reason;

    return this.callTool<CreatePositionResult>(
      'create_position',
      args,
      { retries: opts?.retries ?? 2, timeoutMs: opts?.timeoutMs ?? 15_000 },
    );
  }

  /**
   * Fully close an existing position.
   * Cancels SL/TP then executes close.
   */
  async closePosition(
    wallet: string,
    coin: string,
    opts?: McpCallOptions & {
      reason?: string;
      orderType?: CloseOrderType;
      slippagePercent?: number;
      ensureExecutionAsTaker?: boolean;
    },
  ): Promise<ClosePositionResult> {
    const args: Record<string, unknown> = {
      strategyWalletAddress: wallet,
      coin,
    };
    if (opts?.reason) args.reason = opts.reason;
    if (opts?.orderType) args.orderType = opts.orderType;
    if (opts?.slippagePercent !== undefined) args.slippagePercent = opts.slippagePercent;
    if (opts?.ensureExecutionAsTaker !== undefined) args.ensureExecutionAsTaker = opts.ensureExecutionAsTaker;

    try {
      const data = await this.callTool<Record<string, unknown>>(
        'close_position',
        args,
        { retries: opts?.retries ?? 2, timeoutMs: opts?.timeoutMs ?? 30_000 },
      );
      const resultText = JSON.stringify(data);
      if (resultText.includes('CLOSE_NO_POSITION')) {
        return { success: true, result: 'position_already_closed' };
      }
      return {
        success: true,
        closedPrice: data.closedPrice as number | undefined,
        closedSize: data.closedSize as number | undefined,
        cancelledSlTpOrders: data.cancelledSlTpOrders as number[] | undefined,
        executionAsMaker: data.executionAsMaker as boolean | undefined,
        result: resultText,
      };
    } catch (err) {
      const errStr = String(err);
      if (errStr.includes('CLOSE_NO_POSITION')) {
        return { success: true, result: 'position_already_closed' };
      }
      return { success: false, result: errStr };
    }
  }

  /**
   * Edit an existing position: resize, adjust leverage, set/cancel TP/SL,
   * or flip direction.
   */
  async editPosition(
    wallet: string,
    params: EditPositionParams,
    opts?: McpCallOptions,
  ): Promise<EditPositionResult | null> {
    const args: Record<string, unknown> = {
      strategyWalletAddress: wallet,
      coin: params.coin,
    };
    if (params.direction) args.direction = params.direction;
    if (params.leverage !== undefined) args.leverage = params.leverage;
    if (params.leverageType) args.leverageType = params.leverageType;
    if (params.targetMargin !== undefined) args.targetMargin = params.targetMargin;
    if (params.orderType) args.orderType = params.orderType;
    if (params.slippagePercent !== undefined) args.slippagePercent = params.slippagePercent;
    if (params.ensureExecutionAsTaker !== undefined) args.ensureExecutionAsTaker = params.ensureExecutionAsTaker;
    if (params.takeProfit) args.takeProfit = params.takeProfit;
    if (params.stopLoss) args.stopLoss = params.stopLoss;
    if (params.reason) args.reason = params.reason;

    return this.callToolSafe<EditPositionResult>(
      'edit_position',
      args,
      { retries: opts?.retries ?? 2, timeoutMs: opts?.timeoutMs ?? 15_000 },
    );
  }

  /**
   * Cancel a specific resting order by order ID.
   * Idempotent — already-cancelled/filled orders return success.
   */
  async cancelOrder(
    wallet: string,
    orderId: number,
    opts?: { coin?: string; reason?: string },
  ): Promise<{ success: boolean; wasAlreadyCancelled?: boolean }> {
    const args: Record<string, unknown> = {
      strategyWalletAddress: wallet,
      orderId,
    };
    if (opts?.coin) args.coin = opts.coin;
    if (opts?.reason) args.reason = opts.reason;

    try {
      const data = await this.callTool<Record<string, unknown>>(
        'cancel_order',
        args,
        { retries: 2, timeoutMs: 10_000 },
      );
      return {
        success: true,
        wasAlreadyCancelled: data.wasAlreadyCancelled as boolean | undefined,
      };
    } catch {
      return { success: false };
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // Execution queries
  // ════════════════════════════════════════════════════════════════════

  /**
   * Get the status of a specific order.
   */
  async getOrderStatus(
    wallet: string,
    orderId: number,
  ): Promise<OrderStatus | null> {
    return this.callToolSafe<OrderStatus>(
      'execution_get_order_status',
      { user: wallet, orderId },
    );
  }

  /**
   * Get detailed info for a single open position (includes orders that built it).
   */
  async getOpenPositionDetails(
    traderAddress: string,
    coin: string,
  ): Promise<OpenPositionDetails | null> {
    return this.callToolSafe<OpenPositionDetails>(
      'execution_get_open_position_details',
      { traderAddress, coin },
    );
  }

  // ════════════════════════════════════════════════════════════════════
  // Discovery (historical track record)
  // ════════════════════════════════════════════════════════════════════

  /**
   * Fetch closed positions for a trader.
   * Use for trade history analysis, win rate, PnL patterns.
   */
  async getTraderHistory(
    traderAddress: string,
    opts?: {
      sortBy?: 'CLOSED_TIME' | 'REALIZED_PNL' | 'ENTRY_PRICE' | 'COIN' | 'LEVERAGE' | 'TOTAL_FILLS' | 'TOTAL_FEES';
      sortDirection?: 'ASC' | 'DESC';
      limit?: number;
      offset?: number;
      latest?: boolean;
    },
  ): Promise<ClosedPosition[]> {
    const args: Record<string, unknown> = {
      trader_address: traderAddress,
      sort_by: opts?.sortBy ?? 'CLOSED_TIME',
      sort_direction: opts?.sortDirection ?? 'DESC',
      limit: opts?.limit ?? 20,
      latest: opts?.latest ?? true,
    };
    if (opts?.offset) args.offset = opts.offset;

    const data = await this.callToolSafe<{ closed_positions: ClosedPosition[] }>(
      'discovery_get_trader_history',
      args,
    );
    return data?.closed_positions ?? [];
  }

  // ════════════════════════════════════════════════════════════════════
  // Notifications
  // ════════════════════════════════════════════════════════════════════

  /** Send a Telegram notification. Silently fails. */
  async sendNotification(target: string, message: string): Promise<void> {
    await this.callToolSafe(
      'send_telegram_notification',
      { target, message },
      { retries: 2, timeoutMs: 10_000 },
    );
  }
}
