# Fleet Patches

Cross-fleet patches that apply to **every agent's next v-bump**, not as separate
ferries. When an agent gets a version bump, the operator should verify each
applicable patch is incorporated.

These patches are mechanical and thesis-agnostic. They protect against
fleet-wide bugs and operational hazards that surfaced from observation across
multiple agents.

---

## FP-001 — Wake-up window (low-liquidity quiet hours)

**Pattern observed:** Several agents pile into trades immediately after
00:00 UTC daily-cap reset. Notional spike at midnight UTC corresponds to
fleet-wide ~13–17% of trades vs. a uniform-distribution baseline of ~6%,
2–3x clustered. 00:00 UTC is also a structurally bad market hour for many
asset classes — Asia-overnight thin books, post-funding-settle noise,
US/EU sessions closed.

**Patch:** Producers must skip emission during a configurable low-liquidity
window. Default `00:00 UTC → 04:00 UTC`. Per-agent overrides:

| Agent | Quiet hours (UTC) | Apex bypass score |
|---|---|---|
| Pangolin (funding fade) | 22:00 → 02:00 (just before next funding tick) | 12+ |
| Roach / RoachB (Striker rank-jump) | 00:00 → 04:00 | 12+ |
| Scorpion (multi-asset trend) | 00:00 → 06:00 | 12+ |
| Polar / Kodiak (single-asset alpha) | 00:00 → 13:00 (US session start) | 12+ |
| Dire (BRENTOIL news-driven) | 00:00 → 04:00 | 12+ |
| Otter (OI velocity hunter) | 00:00 → 04:00 | 12+ |
| Mantis (cross-asset lag) | none (signal-driven, no daily-cap bias) | n/a |
| Spider (portfolio operator) | none (low-frequency, daily-decision agent) | n/a |

**Implementation:** `quietHoursStartUtc` + `quietHoursEndUtc` +
`quietHoursApexBypassScore` in agent's config JSON. Producer skips emission
unless current UTC hour is outside the window OR setup score >= apex bypass.

---

## FP-002 — User-conversation Claude sessions must NOT trade

**Pattern observed:** Multiple agents have been opening positions
immediately (within 60–120s) after a user pings them on Telegram with a
status-check message. The Claude Code session that responds to the user
has access to the same MCP toolset (`create_position`, `close_position`,
`edit_position`, `ratchet_stop_*`, `cancel_order`) as the producer cron.
Performance-pressure bias + tool availability + ambiguous "you're an
active trader" prompt language = trades fire to "demonstrate activity."

**Patch:** Add a hard rule to every agent's `SKILL.md` (and to every
runtime.yaml `decision_prompt` for v2 agents with LLM gates):

> **User-conversation Claude sessions MUST NOT call any of:**
> `create_position`, `close_position`, `edit_position`,
> `ratchet_stop_add`, `ratchet_stop_edit`, `ratchet_stop_delete`,
> `cancel_order`, `strategy_close`, `strategy_close_positions`.
>
> These tools are reserved for the **producer cron** (entry path) and
> the **DSL ratchet engine** (exit path). User-conversation sessions
> are read-only.

If the user asks a question that implies action ("anything close to
triggering?"), respond by reading the current state — DO NOT execute.
The producer cron will handle real signals on its next tick.

**Future enforcement (architectural):** at the MCP layer, gate
`create_position` so it requires a `producer_signal_id` in the call
context. Without one, reject. Makes the bypass impossible regardless of
LLM behavior. Coordinate with Daniel for runtime patch.

---

## FP-003 (candidate) — Pattern completeness, not just score-summing

**Pattern observed (Dire v1.0):** Score-floor gates with summed soft
confirmations let through "score 9 with 4 weak confirmations" entries that
closed at protective exits. The +57% peak runner on Apr 29 had score 11
with **all 5 confirmations firing simultaneously**. Pattern-completeness
is more predictive than score-magnitude.

**Patch:** Where applicable, add an "all soft components positive"
gate alongside the score-floor check. For Dire: Volume + OI velocity +
SM premium + Price cleanliness must all contribute >= 1, in addition to
the 4TF/SM hard gates and `score >= minScore`.

**Status:** First applied in Dire v1.6.0 with `requireAllConfirmations: true`
config flag. Candidate for fleet-wide adoption pending observation of
Dire's post-patch trade frequency and outcome distribution.

---

## FP-004 (open) — DSL ratchet exchange-SL synchronization (UPSTREAM)

**Bug observed (Dire, Apr 29 → May 1):** DSL engine internally tracked
peak ROE 57.15% correctly and entered Phase 2. But the exchange-side Stop
Limit order was placed at position open ($106.05 trigger) and **never
updated** as Phase 2 tiers fired. Audit log shows zero
`ratchet_stop_edit` / `cancel_order` events across the entire run-up.
Position retraced from +57% peak to +16% with the original entry-level
stop still in place — runner protection effectively unarmed.

Additionally: today's runtime restart re-baselined the DSL trailing-stop
HW from current mid price instead of reloading from chain-stored peak.

**Status:** Reported to Erik via Telegram 2026-05-01. Backend fix.
**Not a per-skill patch** — affects every v1-runtime agent holding a
position. Pending team investigation. Documented here for cross-reference;
no per-skill action.

---

## Applying these patches

When bumping any agent's version:

1. Check FP-001 — does the producer have quiet hours configured? If not,
   add `quietHoursStartUtc` / `quietHoursEndUtc` / `quietHoursApexBypassScore`
   to the agent's config and the corresponding skip logic to its producer.
2. Check FP-002 — does the agent's SKILL.md have the hard rule against
   trading-tool calls during user-conversation Claude sessions? If not,
   add it. For v2 agents with LLM gates in `runtime.yaml`, also add the
   constraint to the `decision_prompt`.
3. Check FP-003 (if applicable to the agent's signal architecture) —
   does the producer require pattern completeness or just score-summing?
4. FP-004 is upstream — no per-skill action needed.

---

## Changelog

- 2026-05-01: FP-001 + FP-002 + FP-003 candidate first applied in Dire v1.6.0.
- 2026-05-01: FP-004 logged after Dire DSL state mismatch diagnosis.
