"""Needle guards on quant-desk 2.x: the skill is doctrine and narration over the runtime's
`openclaw senpi quant` verb. The engine, its state and its test suite live in @senpi-ai/runtime.

What a rewrite must not drop: the one-sentence card routing and its tool-absent fallback, the
runtime's refusal codes and exit codes, the exec timeout, the whose-book doctrine, and the kept
doctrine rules (never invent a number, never add leaks up, custody language) verbatim.
What it must not bring back: the old script engine, flags the runtime does not run, an absolute
skills path, or a hardcoded coverage figure.

Run:  python3 -m pytest quant-desk/tests -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.normpath(os.path.join(HERE, ".."))
SKILL = os.path.join(SKILL_DIR, "SKILL.md")
REFS = os.path.join(SKILL_DIR, "references")
README = os.path.join(SKILL_DIR, "..", "README.md")

REFUSAL_CODES = ("E_QUANT_IN_PROGRESS", "E_QUANT_NO_ACTIVITY", "E_QUANT_NOT_A_TRADER",
                 "E_QUANT_UNSUPPORTED", "E_QUANT_UPSTREAM", "E_QUANT_TIMEOUT")
SUPPORTED_FLAGS = {"days", "other", "mine", "book", "fresh", "force", "section", "json", "no-wait"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _skill():
    return _read(SKILL)


def _flat(text):
    """Whitespace-normalised, so a needle survives re-wrapping of the Markdown."""
    return " ".join(text.split())


def _frontmatter():
    m = re.match(r"\A---\n(.*?)\n---\n", _skill(), re.S)
    assert m, "SKILL.md has no frontmatter block"
    return m.group(1)


def _needles(*needles):
    flat = _flat(_skill())
    missing = [n for n in needles if _flat(n) not in flat]
    assert not missing, "missing from SKILL.md:\n  " + "\n  ".join(missing)


def _markdown_files():
    for root, _dirs, files in os.walk(SKILL_DIR):
        for name in sorted(files):
            if name.endswith(".md"):
                yield os.path.join(root, name)


# --- frontmatter -------------------------------------------------------------------------------

def test_frontmatter_is_2_2_0_and_requires_the_runtime():
    """Still major 2: boxes gate skill majors on the runtime's manifest ceiling (quant-desk maxMajor 2),
    so 2.x only lands where the runtime has the `senpi quant` verb. The Set stop loss doctrine is a minor:
    on a runtime without the button its text never says `Set stop loss button:`, and rule 5 holds as before.
    The trailing doctrine is a minor too: on a runtime that does not offer it the text never says
    `Trailing stop offered:`."""
    meta = yaml.safe_load(_frontmatter())
    assert meta["name"] == "quant-desk"
    assert meta["metadata"]["version"] == "2.2.0", meta["metadata"]["version"]
    assert "senpi-trading-runtime" in (meta["metadata"].get("requires") or []), meta["metadata"]


def test_suggested_prompts_and_triggers_survive():
    """The in-product chips are buttons: they must match verbatim or the route breaks."""
    _needles("Run quant desk on your Hyperliquid wallet", "Run quant desk on any Hyperliquid wallet",
             "Score my trading", "Find leaks on your Hyperliquid wallet",
             "Find traders for me to analyze with quant desk", "Run quant desk", "What did I miss?",
             '"run AI quant", "run ai-quant", "run quant", "run quant desk", "run quant-desk"',
             "find leaks on my wallets", "NOT for choosing or deploying a strategy")


# --- the runtime surface -----------------------------------------------------------------------

def test_only_the_runtime_verbs_are_taught():
    flat = _flat(_skill())
    used = set(re.findall(r"openclaw senpi quant ([a-z]+)", flat))
    assert used == {"run", "status", "show", "list"}, used
    assert "openclaw senpi guide quant" in flat


def test_only_supported_flags_are_taught():
    """--deep/--find/--compare are refused by the runtime ([E_QUANT_UNSUPPORTED]); the address-book
    flags no longer exist. Teaching any of them sends the model into a refusal or a missing file."""
    used = set(re.findall(r"(?<![\w-])--([a-z][a-z-]*)", _skill()))
    assert used <= SUPPORTED_FLAGS, sorted(used - SUPPORTED_FLAGS)


def test_no_script_engine_is_mentioned():
    for path in _markdown_files():
        text = _read(path)
        for needle in ("desk.py", "scripts/", "/tmp/quant-desk", "addresses.json"):
            assert needle not in text, f"{os.path.relpath(path, SKILL_DIR)} mentions {needle}"


def test_no_absolute_skills_path():
    for path in _markdown_files():
        assert "/data/.openclaw" not in _read(path), os.path.relpath(path, SKILL_DIR)


def test_under_250_lines():
    n = len(_skill().splitlines())
    assert n < 250, f"SKILL.md is {n} lines; the ceiling is 249"


def test_refusal_codes_are_named():
    text = _skill()
    missing = [c for c in REFUSAL_CODES if f"[{c}]" not in text]
    assert not missing, missing


def test_run_id_line_and_exit_codes():
    _needles("`[quant-desk] run `",
             "Exit `2` is a refusal and exit `3` a failure",
             "Exit `6` is still running",
             "Exit `1` is transport")


def test_exec_timeout_and_process_poll():
    """`run` blocks 30-180s and the runtime stops a run at 240s: a shorter exec timeout SIGTERMs
    the CLI mid-desk. A `{"status":"running"}` handoff is polled, never relaunched."""
    _needles("give the exec call a `timeout` of **300** seconds",
             "poll that session with the `process` tool until it ends, inside the same turn")


def test_one_desk_at_a_time_is_the_runtimes_rule():
    _needles("**ONE desk at a time, and NEVER re-run one that is still going.**",
             "second `run` is refused with `[E_QUANT_IN_PROGRESS]`",
             "poll it with `openclaw senpi quant status <runId>`")


def test_the_stages_relay_from_the_stored_run():
    _needles("**Relay it in STAGES — never as one block.**",
             "`openclaw senpi quant run <0xaddress> --section overview`",
             "`openclaw senpi quant show <runId> --section protection`",
             "`openclaw senpi quant show <runId> --section leaks`",
             "never end a turn mid-desk, and never announce a stage you are not about to run",
             'Relay it; do not recompute, reorder or "improve" its numbers.',
             "**Print the desk's header line exactly as the engine emits it",
             'Write **onchain**, never "on-chain", everywhere.')


# --- the card ----------------------------------------------------------------------------------

def test_the_card_routing_sentence():
    _needles("**The next steps go to the card:** call `show_widget` with "
             "`widget_type: \"quant_desk_recommendations\"` and `run_id` set to the run id, nothing else.")


def test_the_card_routing_is_one_sentence():
    """One routing sentence. Restating the tool's when/not-when text in the skill is the verbose-skill
    failure; the widget description owns it."""
    text = _skill()
    assert text.count("quant_desk_recommendations") == 1, text.count("quant_desk_recommendations")
    assert "`items`" not in text, "the card takes run_id only; never teach item ids"


def test_card_closing_and_tool_absent_fallback():
    _needles("When the card is shown, the closing is one framing sentence, not the list",
             "If `show_widget` is not available in this host, relay the desk's next-steps section as prose")


# --- kept doctrine -----------------------------------------------------------------------------

def test_kept_doctrine_is_verbatim():
    _needles("**Never invent a number.**",
             "**Counterfactual, not history, on every leak.**",
             "**Never add the leaks up — quote the one number the desk gives you.**",
             "Summing them produced $68k on a book that lost $65k.",
             "the honest combined figure is the best single lever plus fees, ~$9.5k/yr.",
             "**Never a call to buy or sell a coin.**",
             "senpi cannot put a stop on a position held in the reader's own wallet except through the Set stop loss button.",
             "**name the naked positions and ask how you can help.**",
             "As of 2026-09-30 the button's fixed stop loss and, where the text offers it, its trailing stop are the only stops senpi offers on a custodied book.",
             "Never imply senpi holds or moves their funds.",
             'Never "report", "analyst", "bot", "AI assistant".',
             "**Your quant reads any book on Hyperliquid, not just yours.**",
             "**Never invent an address.**",
             "Protection outranks both when a position is unprotected **and** near liquidation",
             '"is that deliberate?"',
             "senpi cannot place a stop on a book the reader custodies",
             "say *hire my quant*")


def test_whose_book_doctrine_survives():
    _needles("Your own book, or do you want me to find you someone to read?",
             "**do not forget an address they already claimed**",
             "`openclaw senpi quant list`",
             "**A senpi user's perp history lives in their strategy wallets, not their embedded wallet.**",
             "**offer the strategy wallets first**",
             "**Include CLOSED and PAUSED strategies, not just ACTIVE.**",
             "`openclaw senpi quant run 0x… 0x… 0x… --book --section overview`",
             "**Use `--other` whenever the request is about someone else**",
             "**offer to show them the desk on a real book in the same breath**")


def test_not_a_trader_doctrine_survives():
    _needles("makes **no claim about who the reader is**",
             "relay its `say_to_the_reader` line and offer their own wallet",
             "never `--force` unless the reader explicitly asks to read a vault as if it were a trader")


def test_the_coverage_figure_comes_from_the_runtime():
    """Skills carry no figures: the indexed-wallet count is the runtime's to print."""
    for path in _markdown_files():
        text = _read(path)
        assert "26,188" not in text and "coverage.json" not in text, os.path.relpath(path, SKILL_DIR)
    _needles('if it gives none, say "over 25,000"')


# --- tree shape, references, README ------------------------------------------------------------

def test_the_engine_left_the_skill():
    assert not os.path.exists(os.path.join(SKILL_DIR, "scripts")), "scripts/ moved to the runtime"
    tests = sorted(n for n in os.listdir(HERE) if n != "__pycache__")
    assert tests == ["test_skill_text.py"], tests
    refs = sorted(os.listdir(REFS))
    assert refs == ["desk-contract.md", "hire-my-quant.md", "methodology.md"], refs


def test_the_moved_text_travels_as_references():
    contract = _read(os.path.join(REFS, "desk-contract.md"))
    for needle in ("## What the desk is (the output contract, in render order)",
                   "12. **What your quant would do next** — protect first · fix the biggest leak · keep the agents on.",
                   '## Reading the sources (what to say when asked "where does this come from")'):
        assert needle in contract, needle
    handoff = _flat(_read(os.path.join(REFS, "hire-my-quant.md")))
    for needle in ("Good — let's code a strategy that maps to your trading style, while improving some of your leaks.",
                   "Then hand to `senpi-strategy-discover` with the edge as `--theme`.",
                   "Name the leak the template closes"):
        assert needle in handoff, needle
    _needles("`references/desk-contract.md`", "`references/methodology.md`", "`references/hire-my-quant.md`")


def test_methodology_names_where_the_engine_went():
    doc = _flat(_read(os.path.join(REFS, "methodology.md")))
    assert "the quant-desk engine at 1.38.0, the version it had when it moved from" in doc


def test_readme_row_matches_the_skill_version():
    meta = yaml.safe_load(_frontmatter())
    row = re.search(r"\| \[`quant-desk`\]\([^)]+\) \| (\d+\.\d+\.\d+) \|", _read(README))
    assert row, "README row for quant-desk not found"
    assert row.group(1) == meta["metadata"]["version"], (row.group(1), meta["metadata"]["version"])


def test_public_repo_hygiene():
    """Rules only: no user ids and no full wallet addresses in a public skill."""
    for path in _markdown_files():
        text = _read(path)
        assert not re.search(r"\bM\d{5,}\b", text), f"a user id leaked into {path}"
        assert not re.search(r"0x[0-9a-fA-F]{40}", text), f"a wallet address leaked into {path}"



# --- final-review fix wave ----------------------------------------------------------------------

def _stage4():
    flat = _flat(_skill())
    m = re.search(r"\*\*Stage 4 — the rest\*\*(.*?)\*\*All four stages", flat)
    assert m, "stage 4 not found"
    return m.group(1)


def test_find_traders_has_one_named_route():
    """The proven cohort and the leaderboard are not tools; `--find` is gone. The one route is the
    trader-research skill, and candidate search is not claimed missing."""
    _needles("resolve candidates with the `senpi-trader-research` skill, then run the pick with `--other`")
    flat = _flat(_skill())
    assert flat.count("resolve candidates with the `senpi-trader-research` skill") == 2, "7c and branch (4)"
    assert "the proven cohort, the leaderboard" not in flat
    assert "candidate search" not in flat
    assert "this week's top traders right now" not in flat
    _needles("Deep dives and side-by-side compares are not in this version (`[E_QUANT_UNSUPPORTED]`).")


def test_stage_4_leaves_next_to_the_card():
    assert "--section next" not in _stage4(), _stage4()
    assert "--section followups" in _stage4()
    _needles("relay the desk's next-steps section as prose (`show <runId> --section next`)")


def test_analyst_desks_have_no_card():
    _needles("Own-book desks only; an `--other` desk has no card — close with rule 7's *what to take from "
             "this trader* from `--section next`.")


def test_book_path_is_staged():
    _needles("`run 0x… 0x… 0x… --book --section overview`, then rule 1's stages")


def test_list_shows_whose_book():
    _needles("`openclaw senpi quant list` shows the last 20 runs with their voice (mine / other); a `mine` "
             "row means the desk was read in the reader's voice, not that they claimed it — when unsure "
             "whose it is, ask.")
    assert "every address this box has read" not in _flat(_skill())


def test_not_indexed_makes_no_team_promise():
    flat = _flat(_skill())
    assert "flagged it to the team" not in flat
    assert "they'll let you know" not in flat
    _needles("in waves", "Never promise a date.")


def test_no_activity_points_to_7b():
    _needles("if it says the wallet is not indexed yet → rule 7b")


def test_json_is_never_relayed():
    flat = _flat(_skill())
    assert "is for your own lookups" not in flat
    _needles("`--json`: never relay from it — relay the rendered sections.")


def test_methodology_offers_no_deep_modes():
    """Deep modes are not in 2.0, and a full protect-stop formula invites a hand-computed stop."""
    doc = _flat(_read(os.path.join(REFS, "methodology.md")))
    assert ("replay / compare / watch / the protect stop ladder are not in 2.0 — say so; never compute "
            "them from this sheet.") in doc
    for needle in ("* `replay` —", "* `compare` —", "`watch` — the corresponding", "hard stop = 1.5 ×",
                   "a formula change there updates this file in the same release"):
        assert needle not in doc, needle
    assert "if the desk's header version is newer, say the formula may have changed" in doc
    assert not re.search(r"^#+ .*\.py", _read(os.path.join(REFS, "methodology.md")), re.M), \
        "section headings still name deleted scripts"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ✓ {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} passed")


# --- fix wave 2: the skill's words match what the runtime does ---------------------------------

def test_run_id_is_prefix_matched():
    """A cached run suffixes the run line; a first-line / exact-match reading loses the id."""
    _needles("keep the run id from the stderr line that starts with `[quant-desk] run ` (match the "
             "prefix; a cached run adds a suffix)")
    assert "keep the run id from its first line" not in _flat(_skill())


def test_exit_1_is_only_before_a_run_started():
    """The runtime never exits 1 once a run line printed; poll failures are exit 6."""
    _needles("Exit `1` is transport before a run started: say the desk did not run, once, and stop. "
             "If a `[quant-desk] run` line was printed, the desk is running or done — poll "
             "`openclaw senpi quant status <runId>` first.",
             "Exit `6` is still running / state unknown — poll")


def test_freshness_key_names_voice_and_book():
    _needles("the same addresses, `--days`, voice (`--mine`/`--other`) and `--book`",
             "switching voice or book is a new run")


def test_bare_rerun_is_mine_so_other_is_passed_every_time():
    """The runtime keeps no per-address voice memory: a bare re-run is `mine`."""
    flat = _flat(_skill())
    assert "stays someone else's on a bare re-run" not in flat
    _needles("pass `--other` every time")


def test_timeout_row_matches_the_runtime_next_step():
    row = [l for l in _skill().splitlines() if "[E_QUANT_TIMEOUT]" in l]
    assert len(row) == 1, row
    assert "if the reader asks" not in row[0], row[0]
    assert "one more try, then tell the reader it timed out" in row[0], row[0]


def test_book_limit_is_25():
    _needles("a book reads at most 25 wallets; if the reader has more, pass the 25 with the most "
             "recent activity")


# --- the Set stop loss button (runtime widget v2) -----------------------------------------------

def test_the_set_stop_loss_button_is_the_one_exception_to_custody_language():
    """The runtime's result text is the model's only view of the button (history strips the card
    payload): `Set stop loss button:` is the marker it prints, pinned by the runtime's widget test."""
    _needles("**The one exception is the Set stop loss button:** when the card's result text lists "
             "`Set stop loss button:` stop losses, the reader signs those stop losses in their own wallet — tell them to "
             "use **Set stop loss** on the protect card, never say senpi cannot stop those positions, and never "
             "describe a button on any other card or for any position it does not list.")


def test_without_the_marker_the_old_custody_rule_holds():
    _needles("or the card closed with no `Set stop loss button:` in its text, or the prose fallback): the desk "
             "cannot stop them, so **name the naked positions and ask how you can help.**",
             '"set it yourself on Hyperliquid" is the fact, not the offer.')


def test_the_button_never_leaks_into_the_prose_fallback():
    """The prose closing is the no-widget path: there is no card, so there is no button to name."""
    closing = _flat(_skill()).split("## Mandatory closing")[1].split("## No address given")[0]
    assert "Set stop" not in closing, closing
    _needles("senpi cannot place a stop on a book the reader custodies")


def test_the_custody_limit_waits_for_the_card():
    """The marker only arrives with the closing card, after stage 2 and any early protect follow-up:
    stating the limit before it would be contradicted minutes later by a Set stop loss button."""
    _needles("**Before an own-book desk's closing, never state that limit:** at stage 2 and at an early "
             "*protect* follow-up, name the naked positions and say the next steps follow at the end of the desk.")


def test_positions_the_marker_does_not_list_follow_otherwise():
    _needles("A naked position it does not list follows Otherwise.")


def test_a_lone_protection_section_follows_otherwise():
    """`run 0x… --section protection` ("am I protected?") ends without a closing card, so it can never
    carry the marker and never reaches "the end of the desk": it must fall under Otherwise, not hang
    on the hold clause."""
    _needles("Otherwise (a desk with no closing card — a one-question run — or the card closed with no "
             "`Set stop loss button:` in its text, or the prose fallback): the desk cannot stop them",
             "`run 0x… --section protection`")


# --- the trailing stop on the Set stop loss button ------------------------------------------------

def test_trailing_is_mentioned_only_where_the_result_text_offers_it():
    """`Trailing stop offered:` is the runtime's marker (quant-desk-recommendations.ts
    TRAILING_OFFERED_MARKER): the model sees no other evidence that the button can trail."""
    _needles("**Trailing.** When the card's result text also carries `Trailing stop offered:`, the same button can place a "
             "trailing stop instead of the fixed one, for the coins that line lists and no others.",
             "When you mention it, say it trails on Hyperliquid from the price when the order lands, or from a start "
             "price the reader sets (no protection from it until then).")


def test_trailing_is_never_oversold():
    _needles("It is not the leaks section's trailing lock: never attach a leak's figure to it.",
             "Never say it follows the position's size, never say senpi moves or manages it, and never offer it on "
             "any other card.",
             "Without that line, never say the button can place a trailing stop.")


def test_the_old_blanket_ban_on_trailing_is_gone():
    assert "never promise trailing" not in _flat(_skill())


def test_the_trailing_rule_binds_the_button_not_the_word():
    """Rule 3b relays the desk's leaks headline verbatim, and that headline names a trailing stop as a
    counterfactual: a ban on the word would forbid the relay. Only the claim about the button is gated."""
    _needles("a trailing stop that arms at +3% and keeps 50% of the peak")
    assert "do not mention trailing at all" not in _flat(_skill())
