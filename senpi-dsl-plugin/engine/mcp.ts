import { spawn } from "child_process";

const MCP_TIMEOUT_MS = 30_000;

function unwrapMcporterResponse(stdout: string): unknown {
  try {
    const raw = JSON.parse(stdout);
    if (raw && typeof raw === "object" && Array.isArray((raw as { content?: unknown }).content)) {
      const content = (raw as { content: unknown[] }).content;
      const first = content[0];
      if (first && typeof first === "object" && typeof (first as { text?: string }).text === "string") {
        const text = (first as { text: string }).text.trim();
        if (text) return JSON.parse(text);
      }
    }
    return raw;
  } catch {
    return null;
  }
}

export async function execMcp(
  service: string,
  method: string,
  args: Record<string, unknown>
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const proc = spawn("mcporter", ["call", service, method, "--args", JSON.stringify(args)], {
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    proc.stdout?.on("data", (c: Buffer) => { stdout += c.toString("utf8"); });
    proc.stderr?.on("data", (c: Buffer) => { stderr += c.toString("utf8"); });
    const timer = setTimeout(() => {
      proc.kill("SIGTERM");
      reject(new Error(`MCP timeout: ${service} ${method}`));
    }, MCP_TIMEOUT_MS);
    proc.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(stderr || stdout || `exit ${code}`));
        return;
      }
      const raw = unwrapMcporterResponse(stdout);
      resolve(raw);
    });
    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

const DSL_ACTIVE_STATUSES = new Set(["ACTIVE", "PAUSED"]);

export async function strategyGet(strategyId: string): Promise<{ active: boolean; wallet: string | null; error: string | null; confirmedInactive: boolean }> {
  try {
    const raw = await execMcp("senpi", "strategy_get", { strategy_id: strategyId }) as Record<string, unknown>;
    if (raw?.success === false) {
      const err = raw.error as Record<string, unknown> | undefined;
      const msg = err && typeof err === "object" && "message" in err ? String(err.message) : String(err);
      return { active: false, wallet: null, error: msg, confirmedInactive: false };
    }
    const data = (raw?.data ?? raw) as Record<string, unknown>;
    const strategy = data?.strategy as Record<string, unknown> | undefined;
    if (!strategy || typeof strategy !== "object") {
      return { active: false, wallet: null, error: "no strategy in response", confirmedInactive: false };
    }
    const status = String(strategy.status ?? "").trim().toUpperCase();
    if (!DSL_ACTIVE_STATUSES.has(status)) {
      return {
        active: false,
        wallet: null,
        error: `strategy status is ${JSON.stringify(status)} (not ACTIVE/PAUSED)`,
        confirmedInactive: true,
      };
    }
    const wallet = String(strategy.strategyWalletAddress ?? "").trim();
    if (!wallet) {
      return { active: false, wallet: null, error: "no strategyWalletAddress", confirmedInactive: false };
    }
    return { active: true, wallet, error: null, confirmedInactive: false };
  } catch (e) {
    return { active: false, wallet: null, error: String(e), confirmedInactive: false };
  }
}

export async function getClearinghouse(wallet: string): Promise<{ data: Record<string, unknown> | null; error: string | null }> {
  try {
    const raw = await execMcp("senpi", "strategy_get_clearinghouse_state", { strategy_wallet: wallet }) as Record<string, unknown>;
    const data = (raw?.data ?? raw) as Record<string, unknown>;
    if (!data || typeof data !== "object") return { data: null, error: "invalid or empty response" };
    return { data, error: null };
  } catch (e) {
    return { data: null, error: String(e) };
  }
}

export function getActivePositionCoins(data: Record<string, unknown>): Set<string> {
  const coins = new Set<string>();
  for (const section of ["main", "xyz"] as const) {
    const sec = data[section] as Record<string, unknown> | undefined;
    if (!sec) continue;
    const positions = (sec.assetPositions ?? sec.asset_positions) as Array<{ position?: Record<string, unknown> }> | undefined;
    if (!Array.isArray(positions)) continue;
    for (const p of positions) {
      const pos = p?.position;
      if (!pos || typeof pos !== "object") continue;
      const coin = pos.coin as string | undefined;
      const szi = Number(pos.szi ?? 0);
      if (coin && szi !== 0) coins.add(coin);
    }
  }
  return coins;
}

export function getPositionFromClearinghouse(data: Record<string, unknown>, asset: string): Record<string, unknown> | null {
  for (const section of ["main", "xyz"] as const) {
    const sec = data[section] as Record<string, unknown> | undefined;
    if (!sec) continue;
    const positions = (sec.assetPositions ?? sec.asset_positions) as Array<{ position?: Record<string, unknown> }> | undefined;
    if (!Array.isArray(positions)) continue;
    for (const p of positions) {
      const pos = p?.position;
      if (!pos || typeof pos !== "object") continue;
      const coin = pos.coin as string | undefined;
      if (!coin) continue;
      const match = coin === asset || (asset.startsWith("xyz:") && coin === asset.split(":")[1]);
      if (!match) continue;
      const szi = Number(pos.szi ?? 0);
      if (szi === 0) continue;
      return pos;
    }
  }
  return null;
}

export async function marketGetPrices(assets: string[], dex: string): Promise<Record<string, string> | null> {
  try {
    const raw = await execMcp("senpi", "market_get_prices", { assets, dex }) as Record<string, unknown>;
    const data = (raw?.data ?? raw) as Record<string, unknown>;
    const prices = data?.prices as Record<string, string> | undefined;
    return prices ?? null;
  } catch {
    return null;
  }
}

export async function allMids(dex: string): Promise<Record<string, string> | null> {
  try {
    const raw = await execMcp("senpi", "allMids", dex ? { dex } : {}) as Record<string, unknown>;
    const data = (raw?.data ?? raw) as Record<string, string> | undefined;
    return data ?? null;
  } catch {
    return null;
  }
}

export async function editPosition(wallet: string, coin: string, stopLossPrice: number, orderType: string = "LIMIT"): Promise<{ success: boolean; error?: string; orderId?: number }> {
  try {
    const raw = await execMcp("senpi", "edit_position", {
      strategyWalletAddress: wallet,
      coin,
      stopLoss: { price: Math.round(stopLossPrice * 1e4) / 1e4, orderType },
    }) as Record<string, unknown>;
    if (raw?.success === false) {
      const err = raw.error as Record<string, unknown> | undefined;
      const msg = err && typeof err === "object" ? String((err as { message?: string }).message ?? err.description ?? err) : String(err);
      return { success: false, error: msg };
    }
    const data = (raw?.data ?? raw) as Record<string, unknown>;
    let oid: number | undefined;
    const ou = data?.ordersUpdated ?? data?.orders_updated;
    if (ou && typeof ou === "object") {
      const sl = (ou as Record<string, unknown>).stopLoss ?? (ou as Record<string, unknown>).stop_loss;
      if (sl && typeof sl === "object") {
        const v = (sl as Record<string, unknown>).orderId ?? (sl as Record<string, unknown>).order_id;
        if (typeof v === "number") oid = v;
        else if (typeof v === "string") oid = parseInt(v, 10);
      }
    }
    if (oid == null) oid = (data?.stopLossOrderId ?? data?.stop_loss_order_id) as number | undefined;
    return { success: true, orderId: oid };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}

export async function strategyGetOpenOrders(wallet: string, dex: string): Promise<{ orders: Record<string, unknown>[]; error: string | null }> {
  try {
    const raw = await execMcp("senpi", "strategy_get_open_orders", { strategy_wallet: wallet, dex }) as Record<string, unknown>;
    const data = (raw?.data ?? raw) as Record<string, unknown>;
    const orders = data?.orders as Record<string, unknown>[] | undefined;
    return { orders: Array.isArray(orders) ? orders : [], error: null };
  } catch (e) {
    return { orders: [], error: String(e) };
  }
}

export async function closePosition(wallet: string, coin: string, reason: string): Promise<{ success: boolean; result?: string; error?: string }> {
  try {
    const raw = await execMcp("senpi", "close_position", {
      strategyWalletAddress: wallet,
      coin,
      reason,
    }) as Record<string, unknown>;
    const out = typeof raw === "object" && raw !== null ? JSON.stringify(raw) : String(raw);
    if (raw?.success === false) {
      const err = raw.error as Record<string, unknown> | string | undefined;
      const msg =
        err && typeof err === "object"
          ? String((err as { message?: string }).message ?? JSON.stringify(err))
          : String(err ?? "unknown error");
      return { success: false, error: msg };
    }
    return { success: true, result: out };
  } catch (e) {
    return { success: false, error: String(e) };
  }
}
