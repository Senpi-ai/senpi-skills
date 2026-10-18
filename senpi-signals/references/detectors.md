# Reading Senpi Signals — what each read means

Senpi Signals is computed by Senpi's backend (signals-service, agent-bridge), not by this skill. It
scans the whole Hyperliquid universe every few minutes and serves the reads, scores and wording that
the Senpi app, the browser extension and this skill all show. `scripts/sweep.py` fetches and formats;
it never computes a signal. The backend's contract is `docs/SIGNALS_API.md` in agent-bridge — this
page is what the agent needs to narrate it.

## What the backend returns

`GET /v1/signals` (the feed, `--print-feed` / `--brief`): `trade` and `news`, each a ranked list of
reads, plus `as_of`, `stale`, `coverage` and `not_measured`.

`GET /v1/signals/{asset}?insight=none` (`--asset X`): one asset's `signals` (every read on it that
clears a feed floor, strongest first), `positioning` (the proven cohort's headcount on it), `hot_4h`
(its row on the live 4h board), `market` (price, 24h move, funding, open interest, volume), and a
plain-words `summary` (the takeaway the extension's card shows). `--json` prints the body as it came,
including the `context` fact sheet (largest proven positions, liquidation map, 30-day funding range).

A read (one item):

| field | meaning |
|---|---|
| `detector` / `family` | which kind of read it is (below) |
| `side` | `long` / `short`; null when there is none by design (a funding extreme is carry) or it was not resolved |
| `trade_score` / `social_score` | the two lenses, 0–100 checklists, not probabilities |
| `band` | `strong` 🔥 80+, `worth_a_look` 🟠 65–79, `context` 🟡 under 65 |
| `credibility` | book depth × how trustworthy the source is |
| `tradeable` | false on a thin book: news feed only |
| `early` | `early` (positioning in, price not moved), `late`, `against`, `no_side`, `price_unread` |
| `read` / `news` | the line the trade feed / the news feed prints — present it as written |
| `when` | `[2h ago · from $41.20]`, or empty when the read cannot be dated (not "fresh") |
| `numbers` | the read's sourced numbers, verbatim |
| `source` | `proven_cohort` (the ONLY source ever called "smart money"), `leaderboard_4h`, `momentum_events`, `cross_asset_flows`, `market`, `unstated` |
| `wallet` / `tier` | the named wallet (`0x12…ab`) and its tier (`proven` / `skilled`) on a whale read |

## The detectors

- **`sm_divergence`** — the proven cohort's headcount majority on one side, the crowd (the 4h board, or
  funding when the name is off the board) on the other. The gap between the two is the story.
- **`whale_open`** — a proven wallet opened a large position recently; the position dates itself, so
  "opened 18 minutes ago" is a fact about the position. Say *opened*, never *added* or *flipped*.
- **`momentum_event`** — the platform's own tiered event: a top trader's 4h PnL crossed a tier, with
  the largest leg named.
- **`cross_asset_laggard`** — a name that usually follows a leader (BTC) and has not yet.
- **`funding_extreme`** — funding at an extreme of the universe: a carry read, never a direction.
- **`oi_surge`** — open interest building fast with price quiet. The backend keeps its own scan
  history, so this read is measured against Senpi's earlier scans; say what the read says and no more.
- Other change reads (`whale_move`, `funding_flip`, `sm_flow`, `sm_positioning_build`,
  `sm_conviction`) are the backend's to switch on. When one appears, its `read` line says what changed;
  never add a "since" it did not state.

`coverage` / `not_measured`: a source the backend could not read this scan is named there and in the
feed's last line. A detector that was never fed is not one that looked and found nothing.

## Framing rules (apply to every signal)
- **Direction is mandatory** — LONG or SHORT. Unknown side → say "side unresolved".
- **A headcount % never travels without the positioned split** — "62% of the proven traders are long,
  5 long vs 2 short among those positioned". Most of the cohort holds no position in a given name.
- **Name the asset — never a bare ticker.** One clause on *what it is*: "ZEC (Zcash, a privacy coin)",
  "OIL (crude oil, an xyz perp)". Majors (BTC/ETH/SOL/HYPE) need no gloss. If you don't know what it
  is, say so rather than posting it blind.
- **Price context on positioning signals.** The recent move and where price sits; `--asset` carries
  price and the 24h move, `market_get_asset_data` has candles.
- **Define the jargon inline** — never "the leaderboard", "top traders" or "4h window" without a
  plain-English gloss the first time (Glossary below).
- **Weight by size; lead with the robust facts.** Thin positioning (few traders, small market) is
  corroboration, never the headline. Never upgrade "leans short (1.23%)" to "smart money is short."
  See `worked-examples.md` (WLFI).

### Glossary (use these plain-English definitions in output)
- **Proven traders / the cohort** — wallets on Hyperliquid with ≥ $1M lifetime realized PnL, "the
  proven money"; the card says how many were read. A **skilled** tier (≥ $500k) may appear on whale
  reads, labelled as such.
- **The 4h leaderboard** — Hyperliquid's *live 4-hour rolling* board: who is winning right now,
  survivorship included. Never call it smart money.
- **4h window** — the last four hours (the live board's rolling window).
- **Cohort headcount %** — what share of the proven cohort holds this position, always with the
  positioned split.
- **% of top traders' 4h gains** (`hot_4h.share_pct`) — of the open profit the 4h leaders hold, the
  share on this name's side.

## Ways to play (the closing question — never in public/tweet copy)
Per detector, the thesis behind SKILL.md's closing question, always shown with its size and stop first
and executed only on the user's yes:
- `sm_divergence` → **align with the smart-money side** (or fade the crowd).
- `whale_open` → **mirror the whale** (senpi-trade mirror) at your budget, with a stop.
- `funding_extreme` → **harvest the funding** — take the side that *collects*, sized for the carry.
- `oi_surge` / `cross_asset_laggard` → position for the build / the catch-up.
Observation stays public; the play stays private.
