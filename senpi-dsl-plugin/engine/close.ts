import { closePosition } from "./mcp.js";

export async function tryClosePosition(
  state: Record<string, unknown>,
  price: number,
  phase: number,
  breachCount: number,
  breachesNeeded: number,
  effectiveFloor: number,
  now: string,
  closeRetries: number,
  closeRetryDelaySec: number
): Promise<{ closed: boolean; closeResult: string | null }> {
  const wallet = String(state.wallet ?? "");
  const coin = state.asset as string;
  if (!wallet) {
    state.pendingClose = true;
    return { closed: false, closeResult: "error: no wallet in state file" };
  }

  const reason = `DSL breach: Phase ${phase}, ${breachCount}/${breachesNeeded} breaches, price ${price}, floor ${effectiveFloor}`;
  for (let attempt = 0; attempt < closeRetries; attempt++) {
    const result = await closePosition(wallet, coin, reason);
    if (result.success && result.result && !result.result.toLowerCase().includes("error")) {
      state.active = false;
      state.pendingClose = false;
      state.closedAt = now;
      state.closeReason = `DSL breach: Phase ${phase}, price ${price}, floor ${effectiveFloor}`;
      return { closed: true, closeResult: result.result };
    }
    const closeResult = `api_error_attempt_${attempt + 1}: ${result.error ?? result.result ?? "unknown"}`;
    if (attempt < closeRetries - 1) {
      await new Promise((r) => setTimeout(r, closeRetryDelaySec * 1000));
    } else {
      state.pendingClose = true;
      return { closed: false, closeResult };
    }
  }
  state.pendingClose = true;
  return { closed: false, closeResult: null };
}
