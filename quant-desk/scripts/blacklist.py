#!/usr/bin/env python3
"""Senpi's market-maker blacklist — the one MM signal that is a FACT rather than a guess.

A market maker's desk is wrong in every line. desk.py already refuses a book wider than the tape
read, but that gate was deliberately demoted from a classifier to a capability statement: effective
fee rate was tried as the line and does not hold (re-sampled over the leaderboard top 30, 13 of 25
wallets would have been refused, VIP traders at 0.42 and 0.50 bp among them). Guessing who someone
is from their fills is wrong often enough to be insulting when it is wrong.

This is the complement, not a replacement: a LOOKUP against the list Senpi's market-maker detector
writes. It makes no inference. A wallet is on the list or it is not, and the answer carries a reason
and a date.

  POST https://hyperliquid-traders.prod.senpi.ai/graphql
  Authorization: Bearer <Senpi user token>     (SENPI_AUTH_TOKEN, same token the MCP client uses)

THE TRAP THIS MODULE EXISTS TO AVOID. The endpoint answers an auth failure with **HTTP 200** and the
error inside the body:

    {"errors":[{"message":"Authorization token is required...",
                "code":"NO_TOKEN_PROVIDED","statusCode":401}],"data":null}

Only flagged wallets come back, so "absent from the response" means "not flagged". Put those two
facts together and the naive client — check `resp.status == 200`, read `data.…blacklist`, treat an
empty list as clean — reports a market maker as CLEAN whenever the token is missing, expired, or the
service is down. That is the exact failure the caller cannot afford, so every error path here raises
`BlacklistUnavailable` and there is no code path that turns a failure into an empty result.

Per the API's own documentation: *an error means the answer is unknown, not clean.*

Other semantics worth stating, each of which is a way to get a wrong answer quietly:
  * `addresses` takes 1-500 per call. Longer lists are batched here; a caller that silently truncated
    at 500 would read every dropped wallet as not flagged.
  * Matching is case-insensitive but `walletAddress` comes back AS STORED, sometimes mixed-case.
    Everything is compared and keyed in lowercase.
  * One address can return MORE than one row when case variants of it are stored. Rows are merged
    per wallet rather than assumed unique.
  * `newestEntryAt` is a freshness hint and **not** a liveness signal: the detector writes only when
    it finds a new market maker, so a healthy detector with nothing to add is indistinguishable from
    a stopped one. Nothing here gates on it — it is passed through for display only.

Stdlib only, matching the rest of this skill.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import urllib.error
import urllib.request

URL = os.environ.get("SENPI_TRADERS_URL", "https://hyperliquid-traders.prod.senpi.ai/graphql")
AUTH = os.environ.get("SENPI_AUTH_TOKEN", "")

# The API's own cap. Batching is not an optimisation here: a caller that sent 600 addresses and got
# rows for the first 500 would read the other 100 as "not flagged".
MAX_PER_CALL = 500

_QUERY = """query Q($i: GetDiscoveryBlacklistInput!) {
  GetDiscoveryBlacklist(input: $i) {
    blacklist { walletAddress reason createdAt }
    newestEntryAt
    fetchedAt
  }
}"""


class BlacklistUnavailable(Exception):
    """The blacklist could not be read, so market-maker status is UNKNOWN.

    Never catch this and continue as though the wallet were clean. Unknown and clean are different
    answers and the caller must be able to tell the reader which one it has.
    """

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _post(payload, url, token, timeout):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:                     # noqa: PERF203
        raise BlacklistUnavailable(f"HTTP {e.code} from the blacklist service", code=f"HTTP_{e.code}")
    except Exception as e:                                   # noqa: BLE001
        raise BlacklistUnavailable(f"{type(e).__name__} talking to the blacklist service")
    try:
        doc = json.loads(raw)
    except ValueError:
        raise BlacklistUnavailable("blacklist service returned a non-JSON body")

    # GraphQL puts errors in the body with HTTP 200. Check them BEFORE looking at `data`, or an auth
    # failure reads as an empty blacklist, i.e. as "nobody is flagged".
    errs = doc.get("errors")
    if errs:
        first = errs[0] if isinstance(errs, list) and errs else {}
        code = first.get("code") or first.get("statusCode")
        raise BlacklistUnavailable(first.get("message") or "blacklist query returned errors",
                                   code=str(code) if code is not None else None)
    data = doc.get("data")
    if not isinstance(data, dict) or data.get("GetDiscoveryBlacklist") is None:
        # `data: null` with no errors array should not happen, but if it does it is still not "clean".
        raise BlacklistUnavailable("blacklist query returned no data")
    return data["GetDiscoveryBlacklist"]


def check(addresses, token=None, url=None, timeout=15, _post_fn=None):
    """Look up `addresses` against the market-maker blacklist.

    Returns {"flagged": {lowercase_addr: {"reason": str, "created_at": str, "as_stored": str}},
             "checked": [lowercase_addr, ...],
             "newest_entry_at": str|None, "fetched_at": str|None}

    A wallet absent from `flagged` is NOT on the list. Raises BlacklistUnavailable if the answer
    could not be obtained — in which case nothing about these wallets is known.
    """
    post = _post_fn or _post
    url = url or URL
    token = AUTH if token is None else token
    if not token:
        # Refusing here rather than sending an unauthenticated request keeps the caller from ever
        # seeing a 200-with-errors shaped like an empty list.
        raise BlacklistUnavailable("no Senpi auth token (SENPI_AUTH_TOKEN) — market-maker status "
                                   "cannot be checked", code="NO_TOKEN_PROVIDED")

    # Dedupe case-insensitively, preserving order, and drop anything that is not an address.
    seen, wanted = set(), []
    for a in addresses or []:
        if not isinstance(a, str):
            continue
        low = a.strip().lower()
        if not low.startswith("0x") or low in seen:
            continue
        seen.add(low)
        wanted.append(low)
    if not wanted:
        return {"flagged": {}, "checked": [], "newest_entry_at": None, "fetched_at": None}

    flagged, newest, fetched = {}, None, None
    for i in range(0, len(wanted), MAX_PER_CALL):
        batch = wanted[i:i + MAX_PER_CALL]
        out = post({"query": _QUERY, "variables": {"i": {"addresses": batch}}}, url, token, timeout)
        for row in (out.get("blacklist") or []):
            stored = (row.get("walletAddress") or "").strip()
            low = stored.lower()
            if not low:
                continue
            # An address can appear more than once (stored case variants). Keep the first reason and
            # the earliest createdAt rather than letting a later row overwrite it.
            prev = flagged.get(low)
            entry = {"reason": row.get("reason") or "market maker",
                     "created_at": row.get("createdAt"), "as_stored": stored}
            if prev is None or (entry["created_at"] or "") < (prev["created_at"] or ""):
                flagged[low] = entry
        newest = out.get("newestEntryAt") or newest
        fetched = out.get("fetchedAt") or fetched
    return {"flagged": flagged, "checked": wanted,
            "newest_entry_at": newest, "fetched_at": fetched}


def is_market_maker(addr, **kw):
    """True/False for one address. Raises BlacklistUnavailable when the answer is unknown."""
    res = check([addr], **kw)
    return (addr or "").strip().lower() in res["flagged"]


def filter_out(addresses, **kw):
    """(kept, dropped) — drop the blacklisted wallets from a list, e.g. a cohort.

    Raises BlacklistUnavailable rather than returning the list unfiltered: a cohort silently
    containing a quoting engine is the thing this is for.
    """
    res = check(addresses, **kw)
    kept, dropped = [], []
    for a in addresses or []:
        if isinstance(a, str) and a.strip().lower() in res["flagged"]:
            dropped.append(a)
        else:
            kept.append(a)
    return kept, dropped
