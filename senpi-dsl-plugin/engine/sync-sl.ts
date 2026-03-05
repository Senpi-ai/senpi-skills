import { editPosition, strategyGetOpenOrders } from "./mcp.js";

function resolveSlOrderIdAfterEdit(
  coin: string,
  triggerPrice: number,
  orders: Record<string, unknown>[]
): number | null {
  const rounded = Math.round(triggerPrice * 1e4) / 1e4;
  for (const o of orders) {
    const oCoin = (o as Record<string, unknown>).coin;
    if (oCoin !== coin) continue;
    const isTrigger = (o as Record<string, unknown>).isTrigger ?? (o as Record<string, unknown>).is_trigger;
    const isPositionTpsl = (o as Record<string, unknown>).isPositionTpsl ?? (o as Record<string, unknown>).is_position_tpsl;
    if (!isTrigger && !isPositionTpsl) continue;
    const tp = Number((o as Record<string, unknown>).triggerPx ?? (o as Record<string, unknown>).trigger_px ?? 0);
    if (Math.abs(tp - rounded) < 1e-6) {
      const oid = (o as Record<string, unknown>).oid ?? (o as Record<string, unknown>).order_id;
      if (oid != null) {
        const n = typeof oid === "number" ? oid : parseInt(String(oid), 10);
        if (!Number.isNaN(n)) return n;
      }
    }
  }
  return null;
}

export async function syncSlToHyperliquid(
  state: Record<string, unknown>,
  effectiveFloor: number,
  now: string,
  dex: string
): Promise<{ success: boolean; slSyncedThisTick: boolean; error?: string }> {
  const wallet = String(state.wallet ?? "");
  const coin = state.asset as string;
  if (!wallet) return { success: false, slSyncedThisTick: false, error: "no wallet in state" };

  const result = await editPosition(wallet, coin, effectiveFloor, "LIMIT");
  if (!result.success) {
    return { success: false, slSyncedThisTick: false, error: result.error };
  }

  let oid = result.orderId;
  if (oid == null) {
    const { orders } = await strategyGetOpenOrders(wallet, dex);
    oid = resolveSlOrderIdAfterEdit(coin, Math.round(effectiveFloor * 1e4) / 1e4, orders) ?? undefined;
  }

  state.lastSyncedFloorPrice = Math.round(effectiveFloor * 1e4) / 1e4;
  state.slOrderIdUpdatedAt = now;
  if (oid != null) state.slOrderId = oid;
  return { success: true, slSyncedThisTick: true };
}
