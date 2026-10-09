#!/usr/bin/env python3
"""The desk's address book — every wallet this box has read, and how we know whose it is.

A reader arrives from Hyperliquid with their own address, joins and is issued Senpi wallets, then
goes and reads other traders. One person, several addresses, and only some of them theirs. The desk
speaks in the second person about a book it believes is the reader's — "you are leaking $6,770 a
year, here is your stop ladder" — so getting that wrong is not a formatting slip, it is advice about
a stranger's trading delivered as if it were yours.

Whose a wallet is comes from the reader's saved wallets or this run's flag, never from an old typed claim:

  saved     a wallet the reader added in Your wallets — their own claim, kept by Senpi (not proof of
            control). Read from `user_get_me`, never stored here. `--my-wallets` lists them with the
            Senpi strategy wallets.
  verified  a Senpi-issued wallet. We know, because we issued it.
  claimed   RETIRED as an ownership source (1.42.0). Older books still hold rows with it; they are
            ignored at read time. A reader saying "that one's mine" makes it theirs for that run only
            (`--mine` / `--claim`), and the desk tells them it is not saved.
  analyzed  someone else's book we read.

The book also records whether Senpi's index has a wallet, which is what lets the desk tell a reader
their address is not indexed yet instead of quietly running on a fraction of their volume.

Per box, so per user: each agent is its own environment. Nothing here is shared between readers, and
it never leaves the box.

Stdlib only.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import datetime
import json
import os
import re
import tempfile

BOOK = "addresses.json"
VERSION = 1

VERIFIED = "verified"
CLAIMED = "claimed"
ANALYZED = "analyzed"

# Higher wins. A relationship never downgrades: reading your own book in analyst mode must not
# demote it to someone else's, or the next plain run would speak to you about yourself in the third
# person — and, worse, a `--other` run on a wallet you own would make the desk forget it is yours.
RANK = {ANALYZED: 0, CLAIMED: 1, VERIFIED: 2}

ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")


# ── VENDORED external-wallets reader, byte-identical in senpi-portfolio/scripts/portfolio.py,
# ── senpi-improve-trades/scripts/review.py and quant-desk/scripts/addresses.py — skills install
# ── standalone, so none may import another. senpi-portfolio/tests/test_name_reader_parity.py fails
# ── the moment the copies drift.
EXTERNAL_OK = "ok"
EXTERNAL_UNAVAILABLE = "unavailable"


def _external_wallets(me):
    """(status, wallets) from a `user_get_me` payload, outer `data` already stripped.

    The keys live inside `user`: `external_wallets_status` ("ok" | "unavailable") and, only when ok,
    `external_wallets` [{address, label, added_at, access}]. status is "ok" or "unavailable";
    wallets is a list only when status is "ok", else None. An ABSENT status key (an MCP older than
    saved wallets) is "unavailable", never [] — unknown is never empty, so this never reads a
    missing key with a default. `access` is the MCP's read-only line, carried verbatim."""
    user = me.get("user") if isinstance(me, dict) and isinstance(me.get("user"), dict) else me
    if not isinstance(user, dict) or user.get("external_wallets_status") != EXTERNAL_OK:
        return EXTERNAL_UNAVAILABLE, None
    rows = user.get("external_wallets")
    if not isinstance(rows, list):
        return EXTERNAL_UNAVAILABLE, None
    wallets = []
    for w in rows:
        if isinstance(w, dict) and isinstance(w.get("address"), str) and w["address"].strip():
            wallets.append({"address": w["address"].strip().lower(), "label": w.get("label"),
                            "added_at": w.get("added_at"), "access": w.get("access")})
    return EXTERNAL_OK, wallets
# ── end external-wallets reader


# ── VENDORED book group key — byte-identical in senpi-portfolio, senpi-improve-trades and quant-desk;
# pinned by senpi-portfolio/tests/test_name_reader_parity.py. Edit all three or none.
def _book_group_key(strat):
    """The book's key for a strategy's wallets — one Senpi strategy is ONE row, with all its wallets.

    The invariant it rests on: one package = one strategy. The deploy verb stamps `skillName = <package
    id>` on every wallet it creates and names each `<id>-<instance>` (bare `<id>` for one instance); a
    re-run ADOPTS the live wallet of that name instead of funding a second, and refuses a wallet stamped
    by another package. So a package's instances are its wallets, and they share this key. A fork is a
    new id, so a new row. A wallet created outside the deploy path has no stamp: nothing says which
    wallets are one strategy, so it is its own row. A duplicate wallet (a deploy race) carries the same
    stamp and lands on the SAME row as an extra wallet — visible in its wallet count, never a second
    strategy.

    The SAME key in every step and every skill: portfolio's `money` step reads no runtime registry (the
    fast slice), so there is no `profile.group` there — grouping by it made one strategy two rows, then
    one. `skill_name` (from strategy_list) is on every read."""
    if strat.get("skill_name"):
        return str(strat["skill_name"])
    return str(strat.get("wallet") or id(strat))
# ── end vendored book group key


# ── VENDORED Senpi main-wallet reader, byte-identical in senpi-portfolio/scripts/portfolio.py,
# ── senpi-improve-trades/scripts/review.py and quant-desk/scripts/addresses.py — skills install
# ── standalone, so none may import another. senpi-portfolio/tests/test_name_reader_parity.py fails
# ── the moment the copies drift: every skill values the main wallet with ONE rule, so their managed
# ── subtotals reconcile.
MAIN_WALLET_LABEL = "Senpi main wallet"
MAIN_WALLET_STABLES = ("USDC", "USDC.E", "USDT")


def _main_wallet_address(me):
    """The Senpi main (embedded) wallet's address from a `user_get_me` payload (outer `data` already
    stripped) — the first wallet whose `walletType` is "embedded". None when the payload names none."""
    if not isinstance(me, dict):
        return None
    user = me.get("user") if isinstance(me.get("user"), dict) else {}
    wallets = me.get("wallets") or user.get("wallets") or []
    for w in wallets if isinstance(wallets, list) else []:
        if not isinstance(w, dict):
            continue
        kind = w.get("walletType") if w.get("walletType") is not None else w.get("type")
        if str(kind if kind is not None else "").lower() == "embedded":
            return w.get("walletAddress") if w.get("walletAddress") is not None else w.get("address")
    return None


def _main_wallet_value(p):
    """The main wallet's idle cash from ONE `account_get_portfolio` payload (forceFetch, outer `data`
    stripped): perps USDC (`total_in_hyperliquid`) + Hyperliquid spot USDC + EVM stablecoins. None when
    the payload isn't a dict (a failed read — unknown, never $0); a dict reads its fields, 0 when absent.

    GetPortfolioV3 nests the fields under `portfolio`; the idle field is `total_in_hyperliquid` (the old
    `total_usdc_in_hyperliquid` is a harmless fallback); spot is NOT inside it (omitting it under-reports
    the idle by exactly the spot balance); a token row's USD value of exactly 0 is the API's
    zero-as-missing sentinel, so it falls back to `formattedBalance` × `tokenPriceInUSD` (a 0 price → 1)."""
    if not isinstance(p, dict):
        return None
    if isinstance(p.get("portfolio"), dict):
        p = p["portfolio"]

    def num(d, *keys, default=0.0):
        for k in keys:
            if isinstance(d, dict) and d.get(k) is not None:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    continue
        return default

    out = {"idle_hl_usdc": num(p, "total_in_hyperliquid", "total_usdc_in_hyperliquid"),
           "spot_usd": num(p, "total_spot_usd_in_hyperliquid"), "evm_usdc": []}
    evm = 0.0
    for tb in p.get("token_balances") if isinstance(p.get("token_balances"), list) else []:
        if not isinstance(tb, dict):
            continue
        sym = tb.get("symbol") if tb.get("symbol") is not None else tb.get("tokenSymbol")
        if str(sym if sym is not None else "").upper() not in MAIN_WALLET_STABLES:
            continue
        amt = num(tb, "usdValue", "usd_value", "amountUsd", "balanceUsd", "balanceInUSD", "amount")
        if amt == 0.0:
            amt = num(tb, "formattedBalance", "amount") * (num(tb, "tokenPriceInUSD", default=1.0) or 1.0)
        chain = next((tb[k] for k in ("chain", "network", "chainName") if tb.get(k) is not None), "EVM")
        if amt:
            out["evm_usdc"].append({"chain": chain, "usd": round(amt, 2)})
            evm += amt
    out["idle_total"] = round(out["idle_hl_usdc"] + out["spot_usd"] + evm, 2)
    return out
# ── end main-wallet reader


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def norm(addr):
    """Addresses are case-insensitive on Hyperliquid; the book is keyed lowercase."""
    a = str(addr or "").strip().lower()
    return a if ADDR_RE.match(a) else None


def path(state_dir):
    return os.path.join(state_dir, BOOK)


def load(state_dir):
    """The book, or an empty one. A corrupt book is replaced, never raised: losing the address book
    must not take the desk down with it."""
    try:
        with open(path(state_dir)) as fh:
            book = json.load(fh)
        if isinstance(book, dict) and isinstance(book.get("addresses"), dict):
            return book
    except (OSError, ValueError):
        pass
    return {"version": VERSION, "addresses": {}}


def save(state_dir, book):
    """Atomic: a half-written book would read as corrupt and be silently discarded on the next run."""
    os.makedirs(state_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".addresses.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(book, fh, indent=2, sort_keys=True)
        os.replace(tmp, path(state_dir))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get(book, addr):
    a = norm(addr)
    return (book.get("addresses") or {}).get(a) if a else None


def relationship(book, addr):
    e = get(book, addr)
    return e.get("relationship") if e else None


def is_mine(book, addr):
    """True only for a wallet we issued. A `claimed` row (pre-1.42.0 books) is no longer read: a
    non-Senpi wallet is the reader's when it is one of their saved wallets."""
    return relationship(book, addr) == VERIFIED


def record(book, addr, relationship=None, indexed=None, digest=None, now=None):
    """Upsert one address. `relationship` only ever moves up the rank; `indexed` and `digest` are
    last-write-wins because they describe this run."""
    a = norm(addr)
    if not a:
        return book
    ts = now or _now()
    addrs = book.setdefault("addresses", {})
    e = addrs.get(a)
    if e is None:
        # no relationship until one is stated: an own-voice run (`--mine` / `--claim` / the default) must
        # not leave the reader's own address behind as "someone else's book" (1.42.0)
        e = {"relationship": relationship, "first_seen": ts, "runs": 0, "indexed": None, "last_desk": None}
        addrs[a] = e
    if relationship and RANK.get(relationship, -1) > RANK.get(e.get("relationship"), -1):
        e["relationship"] = relationship
    if indexed is not None:
        e["indexed"] = bool(indexed)
    if digest is not None:
        e["last_desk"] = digest
    e["last_run"] = ts
    e["runs"] = int(e.get("runs") or 0) + 1
    return book


def saved_note_due(book, addr):
    """True when the book read `addr` as someone else's and has not yet told the reader it now reads it
    as theirs because they added it to Your wallets. Said once per add: `mark_saved_noted` closes it."""
    e = get(book, addr)
    return bool(e) and e.get("relationship") == ANALYZED and not e.get("saved_noted_at")


def mark_saved_noted(book, addr, now=None):
    """The reader was told once. The `analyzed` mark stays: if they remove the wallet, a bare run is
    back to reading it as the stranger's book it was."""
    e = get(book, addr)
    if e:
        e["saved_noted_at"] = now or _now()
    return book


def clear_saved_note(book, addr):
    """The wallet is no longer saved (a successful read says so): a later re-add is told again."""
    e = get(book, addr)
    if e:
        e.pop("saved_noted_at", None)
    return book


def mark_verified(book, wallets, now=None):
    """Senpi-issued wallets, from the account itself. These are the only addresses we can prove."""
    for w in wallets or []:
        a = norm(w)
        if not a:
            continue
        addrs = book.setdefault("addresses", {})
        e = addrs.get(a)
        if e is None:
            addrs[a] = {"relationship": VERIFIED, "first_seen": now or _now(), "runs": 0,
                        "indexed": None, "last_desk": None, "last_run": None}
        else:
            e["relationship"] = VERIFIED
    return book


def mine(book):
    return sorted(a for a, e in (book.get("addresses") or {}).items()
                  if e.get("relationship") == VERIFIED)


def analyzed(book):
    return sorted(a for a, e in (book.get("addresses") or {}).items()
                  if e.get("relationship") == ANALYZED)


def awaiting_index(book):
    """Addresses we read that Senpi's index does not have — the list behind "I've told the team"."""
    return sorted(a for a, e in (book.get("addresses") or {}).items() if e.get("indexed") is False)
