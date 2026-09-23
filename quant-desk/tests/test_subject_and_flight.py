"""quant-desk — the desk survives a big book, and knows when a book is not a trader's.

Every test here traces to one incident: 2026-09-23, a reader asked for their Hyperliquid score,
their agent pointed the desk at HLP Strategy B (Hyperliquid's own market-making vault: 13,722 fills,
172 coins, 177 open positions), and over six minutes launched FIVE concurrent runs of it. One was
SIGTERM'd at the 120s exec timeout, two died with HTTP 429. The reader got nothing.
"""
import os
import subprocess
import sys
import time
from pathlib import Path as _P

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import desk  # noqa: E402
import hl_api  # noqa: E402


# ---------------------------------------------------------------- the cache actually caches
def test_now_ms_is_bucketed_so_two_runs_share_cache_keys():
    """`now_ms` goes into the body of every expensive read, and the cache key is sha1(body). At
    millisecond resolution every run got a unique key, so the reads that dominate runtime never hit
    the cache the module docstring promises — and a desk killed at 120s cost full price again on the
    re-run, into the same rate bucket that killed it."""
    a, b = hl_api.HL(cache_dir=None), hl_api.HL(cache_dir=None)
    assert a.now_ms == b.now_ms, "two clients in the same bucket must agree on 'now'"
    assert a.now_ms % hl_api.NOW_BUCKET_MS == 0
    assert hl_api.NOW_BUCKET_MS <= hl_api.TTL["userFillsByTime"] * 1000, \
        "a bucket longer than the TTL can never produce a hit"
    assert hl_api.NOW_BUCKET_MS <= hl_api.TTL["candleSnapshot"] * 1000


def test_an_explicit_now_ms_is_left_alone():
    """Fixtures and replays pin `now_ms`; bucketing theirs would move every window under them."""
    assert hl_api.HL(cache_dir=None, now_ms=1_790_000_000_123).now_ms == 1_790_000_000_123


def test_the_expensive_reads_are_keyed_on_a_bucketed_now(tmp_path):
    """The real check: same address, two clients, zero network calls the second time."""
    calls = []

    # exercise the cache path without a network: same key logic, no transport
    import json as _j, hashlib as _h

    class Fake(hl_api.HL):
        pass

    def info(self, body):
        key = _h.sha1(_j.dumps(body, sort_keys=True).encode()).hexdigest()
        path = os.path.join(str(tmp_path), key + ".json")
        if os.path.exists(path):
            return _j.load(open(path))
        calls.append(body)
        _j.dump([], open(path, "w"))
        return []
    Fake.info = info
    Fake(cache_dir=None).fills("0x" + "a" * 40, 0)
    first = len(calls)
    Fake(cache_dir=None).fills("0x" + "a" * 40, 0)
    assert first > 0 and len(calls) == first, "the second run re-fetched what the first already had"


# ---------------------------------------------------------------- one desk at a time
def test_a_second_desk_on_the_same_address_is_refused(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        open(os.path.join(str(tmp_path), "flight-0xabc.pid"), "w").write(str(p.pid))
        assert desk._Flight(str(tmp_path), "0xabc").acquire() is False
    finally:
        p.kill()


def test_a_lock_left_by_a_dead_process_never_wedges_the_address(tmp_path):
    """SIGTERM at the exec timeout is exactly how this starts, so the lock MUST be takeable."""
    open(os.path.join(str(tmp_path), "flight-0xdead.pid"), "w").write("999999")
    assert desk._Flight(str(tmp_path), "0xdead").acquire() is True


def test_a_stale_lock_expires_even_if_the_pid_got_reused(tmp_path):
    f = os.path.join(str(tmp_path), "flight-0xold.pid")
    open(f, "w").write(str(os.getpid() + 1 if os.getpid() > 1 else 2))
    os.utime(f, (time.time() - desk._Flight.STALE_S - 60,) * 2)
    assert desk._Flight(str(tmp_path), "0xold").acquire() is True


def test_force_takes_the_lock(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        open(os.path.join(str(tmp_path), "flight-0xabc.pid"), "w").write(str(p.pid))
        assert desk._Flight(str(tmp_path), "0xabc", force=True).acquire() is True
    finally:
        p.kill()


def test_an_unwritable_state_dir_does_not_refuse_the_desk():
    """The lock is a courtesy, not a gate. Failing to take it must never cost a reader their desk."""
    f = desk._Flight("/proc/nonexistent-cannot-write", "0xabc")
    assert f.acquire() is True and f.held is False
    f.release()          # must not raise


def test_release_is_idempotent(tmp_path):
    f = desk._Flight(str(tmp_path), "0xabc")
    f.acquire(); f.release(); f.release()


# ---------------------------------------------------------------- wide books
def test_the_tape_read_is_capped_and_keeps_every_open_position():
    """The candle pull is the most expensive step and scales with how many names a book touches.
    Capping it must never drop a coin the reader has money in — the protection audit is about those."""
    held = [f"H{i}" for i in range(5)]
    book = {"positions": [{"coin": c} for c in held]}
    track = {"coins": {f"V{i}": {"volume": 1000 - i} for i in range(200)}}
    coins = set(held) | set(track["coins"]) | {"BTC", "NOISE1", "NOISE2"}
    meta = {}
    out = desk._tape(coins, book, track, meta)
    assert len(out) == desk.MAX_TAPE_COINS
    for c in held:
        assert c in out, "an open position was dropped from the tape"
    assert "BTC" in out, "the beta benchmark was dropped"
    assert "V0" in out and "V1" in out, "the highest-volume coins were dropped"
    assert meta["tape"]["coins_touched"] == len(coins) and meta["tape"]["dropped"] > 0
    assert any("wide book" in w for w in meta["warnings"])


def test_a_normal_book_is_not_capped_and_says_nothing():
    book = {"positions": [{"coin": "ETH"}]}
    track = {"coins": {"ETH": {"volume": 10}, "BTC": {"volume": 5}}}
    meta = {}
    out = desk._tape({"ETH", "BTC"}, book, track, meta)
    assert out == {"ETH", "BTC"} and "tape" not in meta and not meta.get("warnings")


# ---------------------------------------------------------------- not every address is a trader
def test_a_vault_is_identified_before_the_desk_spends_anything():
    """One call, and it is Hyperliquid's own answer rather than a heuristic."""
    class HL(hl_api.HL):
        def __init__(self):
            super().__init__(cache_dir=None)
            self.asked = []
        def info(self, body):
            self.asked.append(body["type"])
            if body["type"] == "userRole":
                return {"role": "vault"}
            if body["type"] == "vaultDetails":
                return {"name": "HLP Strategy B",
                        "description": "A component market making strategy included in the HLP vault.",
                        "leader": "0x" + "d" * 40}
            raise AssertionError(f"a vault check must not read {body['type']}")
    hl = HL()
    s = hl.subject("0x" + "1" * 40)
    assert s["role"] == "vault" and s["name"] == "HLP Strategy B"
    assert hl.asked == ["userRole", "vaultDetails"], "the gate cost more than two reads"


def test_an_ordinary_wallet_costs_one_read_and_no_vault_lookup():
    class HL(hl_api.HL):
        def __init__(self):
            super().__init__(cache_dir=None)
            self.asked = []
        def info(self, body):
            self.asked.append(body["type"])
            return {"role": "user"}
    hl = HL()
    assert hl.subject("0x" + "2" * 40) == {"role": "user"}
    assert hl.asked == ["userRole"]


def test_the_gate_fails_open():
    """An address we cannot classify is read as an ordinary trader, which is what it almost always
    is. A venue hiccup must not turn into 'we refuse to read your wallet'."""
    class HL(hl_api.HL):
        def info(self, body):
            raise hl_api.HLError("userRole: HTTP 503")
    s = HL(cache_dir=None).subject("0x" + "3" * 40)
    assert s["role"] == "user" and s["unverified"] is True


def test_role_answers_are_cached_for_longer_than_a_desk_takes():
    """What an address IS does not change on a desk's timescale, and the gate runs on every run."""
    assert hl_api.TTL["userRole"] >= 24 * 3600
    assert hl_api.TTL["vaultDetails"] >= 24 * 3600


# ---------------------------------------------------------------- the instruction
def _skill():
    return " ".join((_P(HERE).parent / "SKILL.md").read_text().split())


def test_the_skill_forbids_re_running_a_desk_that_is_still_going():
    sk = _skill()
    assert "ONE desk at a time" in sk
    assert "Launching a second run does not make the first one finish" in sk
    assert "exit 5" in sk and "already_running" in sk
    assert "at least 180s" in sk, "the timeout that SIGTERMs a wide book is not named"


def test_the_skill_tells_the_agent_what_a_vault_exit_means():
    sk = _skill()
    assert "exit 4" in sk and "not_a_trader" in sk
    assert "say_to_the_reader" in sk


def test_the_score_family_is_covered_without_spending_chars_on_it():
    """A reader asked for their *Hyperliquid score* and was sent to two third-party analyzer sites,
    then to the all-time leaderboard, then ran a full desk on a market-making vault. I first fixed
    that by adding "what's my Hyperliquid score" / "my HL score" / "how good a trader am I" to the
    description — and then measured it.

    On 2026-09-23, on a box with the catalog restored (senpi-agent#82), "What's my Hyperliquid
    score?" routed to quant-desk and ran a desk, on a description that contains NONE of those
    phrases. The model got there from "score my trading" plus "scores ANY Hyperliquid address".

    So the additions were not load-bearing, and the description is the one surface where unused
    characters cost something real: the catalog is rendered whole on every turn against a hard
    budget, and blowing it strips EVERY skill's description fleet-wide — which is exactly what
    happened for six days from 17 Sep. Cheaper to rely on the semantics that already work.
    """
    desc = " ".join((_P(HERE).parent / "SKILL.md").read_text().split("license:")[0].lower().split())
    for anchor in ("score my trading", "rate my trading", "scores any hyperliquid address"):
        assert anchor in desc, f"the score family lost the trigger it actually routes on: {anchor!r}"
    for costly in ("what's my hyperliquid score", "my hl score", "how good a trader am i"):
        assert costly not in desc, (
            f"{costly!r} is back on the selection surface — it was measured as unnecessary; "
            "re-add it only with evidence that routing fails without it")


def test_a_reader_with_no_wallet_is_offered_the_product_not_only_a_question():
    """A brand-new user's first ever prompt was the quant-desk chip. The agent followed the ladder
    correctly to 'ask for an address'. One turn, eight seconds, never came back."""
    sk = _skill()
    assert "--find <band>` returns real candidates" in sk
    assert "A question is the one answer that shows them nothing." in sk
