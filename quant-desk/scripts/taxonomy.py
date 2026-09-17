#!/usr/bin/env python3
"""Asset classes for the strategy read. Crypto tiers come from LIVE open interest (majors = top 3 by OI,
large caps = the next 12, alts = the rest); memecoins are the 1000×-denominated `k…` names plus a short,
documented list; xyz groups are senpi-market-pulse's own (verbatim), so the two skills agree."""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
XYZ_GROUPS = {
    "semis_memory":   ["MU", "SNDK", "SKHX", "DRAM", "WDC"],
    "semis_equipment": ["ASML", "TSM", "QCOM", "ARM", "MRVL"],
    "semis_logic":    ["NVDA", "AMD", "AVGO", "SMH"],
    "software_megacap": ["AMZN", "MSFT", "META", "GOOGL", "AAPL", "ORCL", "PLTR"],
    "crypto_proxy":   ["MSTR", "COIN", "HOOD", "CRWV"],
    "other_equity":   ["TSLA", "SPCX", "ZHIPU"],
    "indices":        ["SP500", "XYZ100", "JP225", "KR200", "NIFTY", "VIX"],
    "commodities":    ["GOLD", "SILVER", "COPPER", "BRENTOIL", "NATGAS", "PLATINUM"],
    "macro_fx":       ["DXY", "JPY", "EUR", "GBP"],
}
XYZ_CLASS = {}
for _g, _names in XYZ_GROUPS.items():
    for _n in _names:
        XYZ_CLASS[_n] = ("xyz_" + ("equities" if _g in ("semis_memory", "semis_equipment", "semis_logic", "software_megacap", "crypto_proxy", "other_equity")
                                   else _g))
# memecoins beyond the k-prefix rule — a taxonomy list, never a trading whitelist
MEMES = {"DOGE", "WIF", "FARTCOIN", "PUMP", "TRUMP", "MOODENG", "POPCAT", "PNUT", "PEPE", "SHIB", "BONK", "FLOKI", "GOAT", "CHILLGUY",
         "MEW", "BRETT", "TURBO", "NEIRO", "SPX", "CASHCAT", "PONS", "MELANIA", "AI16Z", "VVV", "GRIFFAIN"}
LABELS = {"majors": "majors", "large_caps": "large caps", "alts": "alts", "memes": "memecoins", "xyz_equities": "equities", "xyz_indices": "indices",
          "xyz_commodities": "commodities", "xyz_macro_fx": "FX / macro", "xyz_other": "other xyz"}


def crypto_tiers(ctxs):
    """(majors, large_caps) from the main-dex contexts, by open interest in USD."""
    rows = []
    for u, c in zip(ctxs[0]["universe"], ctxs[1]):
        try:
            rows.append((float(c["openInterest"]) * float(c["markPx"]), u["name"]))
        except (TypeError, ValueError, KeyError):
            continue
    rows.sort(reverse=True)
    names = [n for _, n in rows]
    return set(names[:3]), set(names[3:15])


def classify(coin, majors, large):
    if coin.startswith("xyz:"):
        return XYZ_CLASS.get(coin[4:], "xyz_other")
    if coin in majors:
        return "majors"
    if coin.startswith("k") and coin[1:2].isupper() or coin in MEMES:
        return "memes"
    if coin in large:
        return "large_caps"
    return "alts"


def label(cls):
    return LABELS.get(cls, cls)
