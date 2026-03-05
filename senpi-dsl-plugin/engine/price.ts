import { marketGetPrices, allMids } from "./mcp.js";

export async function fetchPriceMcp(dex: string, lookupSymbol: string): Promise<{ price: number | null; error: string | null }> {
  const d = (dex ?? "").trim().toLowerCase() === "main" ? "" : (dex ?? "").trim();
  const isXyz = d === "xyz";
  const responseKey = isXyz ? `xyz:${lookupSymbol}` : lookupSymbol;

  const assets = [responseKey];
  const prices = await marketGetPrices(assets, d);
  let priceStr: string | undefined;
  if (prices && typeof prices === "object") {
    priceStr = (prices as Record<string, string>)[responseKey];
  }
  if (priceStr == null) {
    const mids = await allMids(d);
    if (mids && typeof mids === "object") {
      priceStr = (mids as Record<string, string>)[responseKey];
    }
  }
  if (priceStr == null) {
    return { price: null, error: `no price for ${lookupSymbol} (dex=${d || "main"})` };
  }
  const price = parseFloat(priceStr);
  if (Number.isNaN(price)) return { price: null, error: "invalid price" };
  return { price, error: null };
}
