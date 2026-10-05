"""An unreadable blacklist must never read as a clean one.

Context (#763, and Jason in #quant on 2026-09-23: "I see some users getting tripped up by running
Quant Desk on MMs"). desk.py's fee-rate MM classifier was removed because it was wrong: re-sampled
over the leaderboard top 30, 13 of 25 wallets would have been refused, VIP traders at 0.42 and
0.50 bp among them. What replaced it is a breadth cap, which is honest but catches only the wide
books. Senpi's market-maker detector maintains an actual list, and `GetDiscoveryBlacklist` exposes
it — a lookup, not an inference.

THE FAILURE MODE THESE TESTS EXIST FOR. Verified against the live endpoint on 2026-10-05, no token:

    HTTP 200
    {"errors":[{"message":"Authorization token is required...",
                "code":"NO_TOKEN_PROVIDED","statusCode":401}],"data":null}

HTTP **200** on an auth failure, and the API returns only FLAGGED wallets so absence means "not
flagged". A client that trusts the status code and reads the array therefore reports every market
maker as clean the moment the token lapses — turning a safety check into a rubber stamp. Per the
API's own docs: an error means the answer is unknown, not clean.

Run: python3 -m pytest quant-desk/tests/test_mm_blacklist.py -q
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import blacklist  # noqa: E402

MM = "0x956a000000000000000000000000000000000000"
TRADER = "0xdead000000000000000000000000000000000000"


def _responder(payload_by_batch):
    """Stand in for the HTTP POST, recording the batches it was asked for."""
    calls = []

    def post(payload, url, token, timeout):
        batch = payload["variables"]["i"]["addresses"]
        calls.append(batch)
        return payload_by_batch(batch)

    post.calls = calls
    return post


# ── the trap: HTTP 200 carrying an error ──

def test_a_200_with_a_graphql_error_raises_rather_than_reading_as_clean():
    """The exact live shape. If this ever returns a value instead of raising, every market maker is
    reported clean whenever the token is missing or expired."""
    # Fake urlopen rather than the post hook, so the REAL parsing path is what gets tested — the
    # bug would live in the parser, not in a stub.
    import json as _json
    import urllib.request as _ur

    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    body = _json.dumps({"errors": [{"message": "Authorization token is required.",
                                    "code": "NO_TOKEN_PROVIDED", "statusCode": 401}],
                        "data": None}).encode()
    orig = _ur.urlopen
    _ur.urlopen = lambda req, timeout=None: _Resp(body)
    try:
        with pytest.raises(blacklist.BlacklistUnavailable) as ei:
            blacklist.check([MM], token="whatever")
        assert ei.value.code == "NO_TOKEN_PROVIDED", f"the error code is lost: {ei.value.code}"
    finally:
        _ur.urlopen = orig


def test_data_null_with_no_errors_array_also_raises():
    """Shouldn't happen, but `data: null` is still not a clean answer."""
    import json as _json
    import urllib.request as _ur

    class _Resp:
        def read(self): return _json.dumps({"data": None}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    orig = _ur.urlopen
    _ur.urlopen = lambda req, timeout=None: _Resp()
    try:
        with pytest.raises(blacklist.BlacklistUnavailable):
            blacklist.check([MM], token="t")
    finally:
        _ur.urlopen = orig


def test_a_missing_token_raises_instead_of_sending_an_unauthenticated_request():
    """Sending it anyway would return the 200-with-errors shape, which is the trap. Refuse earlier."""
    with pytest.raises(blacklist.BlacklistUnavailable) as ei:
        blacklist.check([MM], token="")
    assert ei.value.code == "NO_TOKEN_PROVIDED"


def test_a_transport_failure_raises():
    def boom(_p, _u, _t, _to):
        raise blacklist.BlacklistUnavailable("ConnectionError talking to the blacklist service")
    with pytest.raises(blacklist.BlacklistUnavailable):
        blacklist.check([MM], token="t", _post_fn=boom)


# ── the happy paths ──

def test_a_flagged_wallet_is_reported_with_its_reason():
    post = _responder(lambda b: {"blacklist": [{"walletAddress": MM.upper(),
                                                "reason": "quotes both sides, 24 coins",
                                                "createdAt": "2026-09-20T10:00:00Z"}],
                                 "newestEntryAt": "2026-10-01T00:00:00Z", "fetchedAt": "now"})
    res = blacklist.check([MM], token="t", _post_fn=post)
    hit = res["flagged"][MM.lower()]
    assert hit["reason"] == "quotes both sides, 24 coins"
    assert hit["as_stored"] == MM.upper(), "the stored casing should be preserved for display"


def test_absence_from_the_response_means_not_flagged():
    """The API returns ONLY flagged wallets — this is the documented semantic, and it is the half
    that makes an error-as-empty-list so dangerous."""
    post = _responder(lambda b: {"blacklist": [], "newestEntryAt": None, "fetchedAt": "now"})
    res = blacklist.check([TRADER], token="t", _post_fn=post)
    assert res["flagged"] == {}
    assert res["checked"] == [TRADER.lower()]


def test_matching_is_case_insensitive_in_both_directions():
    """Input may be mixed-case and `walletAddress` comes back AS STORED. Compare in one casing or a
    flagged wallet slips through on a capital letter."""
    post = _responder(lambda b: {"blacklist": [{"walletAddress": MM.upper(), "reason": "mm",
                                                "createdAt": "2026-09-20T00:00:00Z"}]})
    assert blacklist.is_market_maker(MM.upper(), token="t", _post_fn=post)
    assert blacklist.is_market_maker(MM.lower(), token="t", _post_fn=post)


def test_duplicate_rows_for_one_wallet_collapse():
    """Case variants can produce more than one row for the same address."""
    post = _responder(lambda b: {"blacklist": [
        {"walletAddress": MM.upper(), "reason": "later", "createdAt": "2026-09-25T00:00:00Z"},
        {"walletAddress": MM.lower(), "reason": "earlier", "createdAt": "2026-09-20T00:00:00Z"}]})
    res = blacklist.check([MM], token="t", _post_fn=post)
    assert len(res["flagged"]) == 1
    assert res["flagged"][MM.lower()]["reason"] == "earlier", "keep the earliest flagging"


# ── batching: a silent truncation reads as "not flagged" ──

def test_more_than_500_addresses_are_batched_not_truncated():
    addrs = [f"0x{i:040x}" for i in range(1203)]
    post = _responder(lambda b: {"blacklist": []})
    res = blacklist.check(addrs, token="t", _post_fn=post)
    assert [len(b) for b in post.calls] == [500, 500, 203], (
        f"batches were {[len(b) for b in post.calls]} — the API caps `addresses` at 500 and every "
        f"address dropped by a truncation would read as NOT flagged")
    assert len(res["checked"]) == 1203


def test_duplicates_and_non_addresses_are_dropped_before_batching():
    post = _responder(lambda b: {"blacklist": []})
    res = blacklist.check([MM, MM.upper(), "  " + MM + "  ", "not-an-address", None, 7],
                          token="t", _post_fn=post)
    assert res["checked"] == [MM.lower()]
    assert post.calls == [[MM.lower()]]


def test_an_empty_input_does_not_call_the_service():
    post = _responder(lambda b: {"blacklist": []})
    res = blacklist.check([], token="t", _post_fn=post)
    assert res["flagged"] == {} and post.calls == []


# ── newestEntryAt is a freshness hint, never a gate ──

def test_a_stale_or_absent_newest_entry_does_not_change_the_verdict():
    """The detector writes only when it finds a NEW market maker, so a healthy detector with nothing
    to add looks exactly like a stopped one. Gating on this would refuse every wallet during a quiet
    week, or worse, treat a quiet week as reason to trust an empty answer."""
    flagged = {"blacklist": [{"walletAddress": MM, "reason": "mm", "createdAt": "2026-01-01T00:00:00Z"}],
               "newestEntryAt": None}
    assert blacklist.is_market_maker(MM, token="t", _post_fn=_responder(lambda b: flagged))
    src = open(os.path.join(HERE, "..", "scripts", "blacklist.py"), encoding="utf-8").read()
    for pattern in ("if newest", "if not newest", "newest_entry_at <", "newestEntryAt <"):
        assert pattern not in src, f"blacklist.py gates on freshness ({pattern}) — it must not"


# ── filter_out, for cohort lists ──

def test_filter_out_drops_only_the_flagged_and_keeps_order():
    post = _responder(lambda b: {"blacklist": [{"walletAddress": MM, "reason": "mm",
                                                "createdAt": "2026-09-20T00:00:00Z"}]})
    kept, dropped = blacklist.filter_out([TRADER, MM, "0xabc" + "0" * 37], token="t", _post_fn=post)
    assert kept == [TRADER, "0xabc" + "0" * 37]
    assert dropped == [MM]


def test_filter_out_raises_rather_than_returning_an_unfiltered_cohort():
    def boom(_p, _u, _t, _to):
        raise blacklist.BlacklistUnavailable("down")
    with pytest.raises(blacklist.BlacklistUnavailable):
        blacklist.filter_out([TRADER, MM], token="t", _post_fn=boom)


# ── the wiring in desk.py ──

def test_desk_checks_the_blacklist_before_the_expensive_half():
    """The gate has to sit with the breadth gate, before the hourly-candle pulls and the two cohort
    reads — those are ~60-90s of a ~120s run. Checking afterwards costs the reader the whole run."""
    src = open(os.path.join(HERE, "..", "scripts", "desk.py"), encoding="utf-8").read()
    assert "import blacklist" in src, "desk.py does not import the blacklist"
    i_check = src.index("blacklist.check(")
    i_breadth = src.index("len(_coins) > MAX_TAPE_COINS")
    i_tape = src.index("hl.meta()")
    assert i_check < i_tape, "the blacklist check runs after the expensive reads begin"
    assert abs(i_check - i_breadth) < 3000, "the check drifted away from the breadth gate"


def test_desk_treats_an_unavailable_blacklist_as_a_warning_not_a_clean_bill():
    """It must not refuse the whole desk when one endpoint is down, and it must not imply the wallet
    was verified. Both halves matter: the warning is the only thing standing between the reader and
    a desk silently produced for a possible market maker."""
    src = open(os.path.join(HERE, "..", "scripts", "desk.py"), encoding="utf-8").read()
    assert "except blacklist.BlacklistUnavailable" in src, "desk.py does not handle the unknown case"
    # Anchor inside the SUBJECT gate. There is a second handler in the cohort filter which fails the
    # other way on purpose, and a bare `index()` finds whichever one happens to be defined first.
    subject = src[src.index("_bp = market_maker_rate(fills)"):src.index("len(_coins) > MAX_TAPE_COINS")]
    assert "except blacklist.BlacklistUnavailable" in subject, (
        "the subject gate no longer handles the unknown case (found only the cohort handler)")
    seg = subject[subject.index("except blacklist.BlacklistUnavailable"):]
    assert "warnings" in seg, "an unreadable blacklist produces no warning"
    assert "does NOT confirm" in seg, (
        "the warning does not say the wallet is unverified — 'could not check' alone reads as "
        "incidental, and the reader needs to know the desk is not vouching for the subject")


def test_a_flagged_wallet_is_refused_with_a_reader_facing_message():
    src = open(os.path.join(HERE, "..", "scripts", "desk.py"), encoding="utf-8").read()
    seg = src[src.index("market_maker_blacklisted"):][:1800]
    assert "say_to_the_reader" in seg, "no reader-facing text — the payload is internal-only"
    assert "if_you_meant_it" in seg and "--force" in seg, (
        "no override. The breadth gate offers --force and this should too: the reader may know "
        "exactly what they are looking at.")
    assert "not force" in src[src.index("market_maker_blacklisted") - 600:
                              src.index("market_maker_blacklisted")], (
        "the refusal ignores --force")


# ── the cohorts: the OTHER place a market maker enters the desk ──
#
# The subject gate protects the reader who pastes a quoting engine. The cohorts are what every
# OTHER desk is measured against — both are ranked by realized P&L, which is the leaderboard a
# market maker tops without taking a directional view. The two gates must fail in OPPOSITE
# directions, and that asymmetry is the whole design, so it is pinned here.

def _desk():
    sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
    import desk  # noqa: PLC0415
    return desk


def test_a_blacklisted_wallet_is_dropped_from_a_comparison_cohort():
    desk = _desk()
    meta = {}
    calls = []

    def fake_filter_out(addrs, **kw):
        calls.append(list(addrs))
        return [a for a in addrs if a.lower() != MM.lower()], [MM]

    orig = blacklist.filter_out
    blacklist.filter_out = fake_filter_out
    try:
        kept = desk._drop_market_makers([TRADER, MM], meta, "proven")
    finally:
        blacklist.filter_out = orig
    assert kept == [TRADER], f"the market maker survived the cohort: {kept}"
    assert meta["market_maker_check"]["cohorts_filtered"]["proven"]["dropped"] == 1
    assert calls == [[TRADER, MM]]


def test_an_unreadable_blacklist_leaves_the_cohort_INTACT_and_warns():
    """The opposite of the subject gate, on purpose. A cohort of 100 wallets that could not be
    screened still tells the reader far more than no cohort, so this one fails OPEN — but it has to
    say so, or the reader reads a screened comparison that was never screened."""
    desk = _desk()
    meta = {}

    def boom(_addrs, **kw):
        raise blacklist.BlacklistUnavailable("service down")

    orig = blacklist.filter_out
    blacklist.filter_out = boom
    try:
        kept = desk._drop_market_makers([TRADER, MM], meta, "proven")
    finally:
        blacklist.filter_out = orig
    assert kept == [TRADER, MM], "an unreadable blacklist emptied or trimmed the cohort"
    assert meta["market_maker_check"]["cohorts_screened"] is False
    assert any("may include quoting engines" in w for w in meta["warnings"]), meta.get("warnings")


def test_the_cohort_warning_is_said_once_not_once_per_cohort():
    """Two senpi cohorts plus the public fallback all run through this. Three copies of the same
    sentence reads like three separate faults."""
    desk = _desk()
    meta = {}

    def boom(_addrs, **kw):
        raise blacklist.BlacklistUnavailable("service down")

    orig = blacklist.filter_out
    blacklist.filter_out = boom
    try:
        for label in ("proven", "hot", "public"):
            desk._drop_market_makers([TRADER], meta, label)
    finally:
        blacklist.filter_out = orig
    assert len([w for w in meta["warnings"] if "quoting engines" in w]) == 1, meta["warnings"]


def test_an_empty_cohort_does_not_call_the_service():
    desk = _desk()
    calls = []
    orig = blacklist.filter_out
    blacklist.filter_out = lambda a, **kw: (calls.append(a), (a, []))[1]
    try:
        assert desk._drop_market_makers([], {}, "proven") == []
    finally:
        blacklist.filter_out = orig
    assert calls == []


def test_both_cohort_paths_and_the_public_fallback_are_screened():
    """Three call sites. A cohort that skips the filter is the pollution this exists to remove, and
    the public fallback is the one most likely to be forgotten — it only runs when senpi discovery
    is unavailable, so no normal test run reaches it."""
    src = open(os.path.join(HERE, "..", "scripts", "desk.py"), encoding="utf-8").read()
    assert "_drop_market_makers(fetch(mcp, meta), meta, name)" in src, (
        "the senpi proven/hot cohorts are not screened")
    assert "_drop_market_makers(hl_api.public_cohort(" in src, (
        "the public leaderboard fallback cohort is not screened")
    assert src.count("_drop_market_makers(") == 3, (
        f"expected the definition plus two call sites, found {src.count('_drop_market_makers(')} — "
        f"a new cohort path was added without screening it")


def test_the_two_gates_fail_in_opposite_directions():
    """Stated as a test because it is the one thing a future edit is most likely to 'tidy' into
    consistency. Subject: unknown must STOP the claim. Cohort: unknown must NOT stop the run."""
    src = open(os.path.join(HERE, "..", "scripts", "desk.py"), encoding="utf-8").read()
    subject = src[src.index("_bp = market_maker_rate(fills)"):src.index("len(_coins) > MAX_TAPE_COINS")]
    assert "does NOT confirm" in subject and "warnings" in subject, "the subject gate stopped warning"
    cohort = src[src.index("def _drop_market_makers"):src.index("def analyze(")]
    assert "return addrs" in cohort, "the cohort filter no longer fails open"
    assert "Fails OPEN" in cohort, "the asymmetry is no longer documented where it lives"
