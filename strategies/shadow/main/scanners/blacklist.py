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

THE TRAP THIS MODULE EXISTS TO AVOID. The endpoint used to answer an auth failure with **HTTP 200**
and the error inside the body:

    {"errors":[{"message":"Authorization token is required...",
                "code":"NO_TOKEN_PROVIDED","statusCode":401}],"data":null}

Only flagged wallets come back, so "absent from the response" means "not flagged". Put those two
facts together and the naive client — check `resp.status == 200`, read `data.…blacklist`, treat an
empty list as clean — reports a market maker as CLEAN whenever the token is missing, expired, or the
service is down. That is the exact failure the caller cannot afford, so every error path here raises
`BlacklistUnavailable` and there is no code path that turns a failure into an empty result.

Per the API's own documentation: *an error means the answer is unknown, not clean.*

**As of 2026-10-06 the service answers 401 for an auth failure** (Vignesh shipped it after this was
reported), with the same body. The 200 handling stays — it is one line, the fix is server-side and
could regress, and the GraphQL spec permits it. But urllib raises `HTTPError` BEFORE the body is
read, so the error body is now parsed on that path too; otherwise a 401 would reach the reader as a
bare "HTTP 401" with no indication of which failure it was. Vignesh also noted that every non-auth
error currently comes back as `INTERNAL_ERROR` / 500 rather than a specific code — harmless here
only because any error is treated as unknown.

**THE TABLE IS NOT ALL MARKET MAKERS.** Nine rows carry reasons like `probe`, `test` and
`unauth-write-poc-benign`, written while the internal write endpoint was open, and two genuine
market-maker rows spell the reason `MARKET_MAKER` with an underscore. A client that treats every
returned row as a market maker therefore refuses real traders who happen to sit in the table as
test rows — the same false-accusation failure that killed the fee-rate classifier. So the REASON is
matched, not assumed: only `MARKET MAKER` (any spelling) reaches `flagged`. Everything else lands in
`other`, and an `other` row whose reason is not a known test value is marked `recognised: False`,
because strict matching protects against a false refusal but would otherwise turn a NEW
market-maker spelling into a silent miss — the more dangerous direction.

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

# THE TABLE IS NOT ALL MARKET MAKERS. Vignesh, reviewing this on 2026-10-06: nine rows carry reasons
# like `probe`, `test` and `unauth-write-poc-benign`, written while the internal write endpoint was
# open, and two real market-maker rows spell the reason `MARKET_MAKER` with an underscore. A client
# that treats every returned row as a market maker therefore (a) refuses real traders who happen to
# sit in the table as test rows, and (b) must still catch the underscore spelling. Refusing a real
# trader is the exact failure that killed the fee-rate classifier — 13 of 25 leaderboard wallets,
# VIP traders among them — so the reason is matched, not assumed.
MARKET_MAKER_REASON = "MARKET MAKER"

# Reasons known NOT to mean market maker. Anything outside this set that is also not MARKET MAKER is
# reported as `unrecognised` rather than silently ignored: strict matching protects against a false
# refusal, but it would otherwise turn a NEW market-maker spelling into a silent MISS, which is the
# more dangerous direction. Unrecognised reasons surface so a drift is visible instead of quiet.
KNOWN_NON_MM_REASONS = frozenset({"PROBE", "TEST", "UNAUTH-WRITE-POC-BENIGN"})


def _normalise_reason(reason):
    """`MARKET_MAKER`, `market maker`, ` Market Maker ` -> `MARKET MAKER`."""
    return (reason or "").strip().upper().replace("_", " ")


def is_market_maker_reason(reason):
    """True only for the market-maker reason, in any of its spellings."""
    return _normalise_reason(reason) == MARKET_MAKER_REASON

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
        # As of 2026-10-06 an auth failure answers HTTP 401 rather than 200 (Vignesh shipped this
        # after the trap below was reported). urllib raises before the body is read, so read it here
        # or the GraphQL error inside — the part that says WHICH failure it was — is thrown away and
        # the reader gets a bare "HTTP 401".
        detail, code = None, f"HTTP_{e.code}"
        try:
            err_doc = json.loads(e.read())
            first = (err_doc.get("errors") or [{}])[0]
            detail = first.get("message")
            code = str(first.get("code") or first.get("statusCode") or code)
        except Exception:                                    # noqa: BLE001, S110
            pass                                             # a non-JSON error body is still a failure
        raise BlacklistUnavailable(detail or f"HTTP {e.code} from the blacklist service", code=code)
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

    Returns {"flagged": {lowercase_addr: {"reason", "created_at", "as_stored"}},   # MARKET MAKERS
             "other":   {lowercase_addr: {..., "recognised": bool}},                # in table, NOT MM
             "checked": [lowercase_addr, ...],
             "newest_entry_at": str|None, "fetched_at": str|None}

    `flagged` holds ONLY wallets whose reason is MARKET MAKER (either spelling). A wallet in the
    table for any other reason lands in `other` and is NOT a market maker — refusing one would be a
    false accusation against a real trader. `other[x]["recognised"]` is False when the reason is
    neither MARKET MAKER nor a known test value, which is how a new spelling becomes visible rather
    than a silent miss.

    A wallet absent from `flagged` is NOT a flagged market maker. Raises BlacklistUnavailable if the
    answer could not be obtained — in which case nothing about these wallets is known.
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
        return {"flagged": {}, "other": {}, "checked": [],
                "newest_entry_at": None, "fetched_at": None}

    flagged, other, newest, fetched = {}, {}, None, None
    for i in range(0, len(wanted), MAX_PER_CALL):
        batch = wanted[i:i + MAX_PER_CALL]
        out = post({"query": _QUERY, "variables": {"i": {"addresses": batch}}}, url, token, timeout)
        for row in (out.get("blacklist") or []):
            stored = (row.get("walletAddress") or "").strip()
            low = stored.lower()
            if not low:
                continue
            reason = row.get("reason")
            entry = {"reason": reason, "created_at": row.get("createdAt"), "as_stored": stored}
            if not is_market_maker_reason(reason):
                # Present in the table for some other purpose. NOT a market maker, so it must not
                # reach `flagged` — but it is recorded, with whether we recognise the reason, so a
                # new market-maker spelling shows up instead of disappearing.
                entry["recognised"] = _normalise_reason(reason) in KNOWN_NON_MM_REASONS
                other.setdefault(low, entry)
                continue
            # An address can appear more than once (stored case variants). Keep the earliest
            # flagging rather than letting a later row overwrite it.
            prev = flagged.get(low)
            if prev is None or (entry["created_at"] or "") < (prev["created_at"] or ""):
                flagged[low] = entry
        newest = out.get("newestEntryAt") or newest
        fetched = out.get("fetchedAt") or fetched
    # A wallet with BOTH a market-maker row and a test row is a market maker; drop it from `other`.
    for low in flagged:
        other.pop(low, None)
    return {"flagged": flagged, "other": other, "checked": wanted,
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
