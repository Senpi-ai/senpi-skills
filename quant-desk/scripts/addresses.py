#!/usr/bin/env python3
"""The desk's address book — every wallet this box has read, and how we know whose it is.

A reader arrives from Hyperliquid with their own address, joins and is issued Senpi wallets, then
goes and reads other traders. One person, several addresses, and only some of them theirs. The desk
speaks in the second person about a book it believes is the reader's — "you are leaking $6,770 a
year, here is your stop ladder" — so getting that wrong is not a formatting slip, it is advice about
a stranger's trading delivered as if it were yours.

Two tiers of "theirs", because they are not the same claim:

  verified  a Senpi-issued wallet. We know, because we issued it.
  claimed   the address the reader typed and said was theirs. UNVERIFIABLE from a chat message —
            anyone can paste a whale's address and call it their own.
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
    """True only for a wallet we issued or the reader explicitly claimed."""
    return relationship(book, addr) in (VERIFIED, CLAIMED)


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
        e = {"relationship": ANALYZED, "first_seen": ts, "runs": 0, "indexed": None, "last_desk": None}
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
                  if e.get("relationship") in (VERIFIED, CLAIMED))


def analyzed(book):
    return sorted(a for a, e in (book.get("addresses") or {}).items()
                  if e.get("relationship") == ANALYZED)


def awaiting_index(book):
    """Addresses we read that Senpi's index does not have — the list behind "I've told the team"."""
    return sorted(a for a, e in (book.get("addresses") or {}).items() if e.get("indexed") is False)
