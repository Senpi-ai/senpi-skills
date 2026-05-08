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

## FP-004 (narrowed scope) — DSL ratchet exchange-SL synchronization

**Bug originally observed (Dire, Apr 29 → May 1):** DSL engine internally
tracked peak ROE 57.15% correctly and entered Phase 2. But the exchange-
side Stop Limit order was placed at position open ($106.05 trigger) and
**never updated** as Phase 2 tiers fired. Audit log shows zero
`ratchet_stop_edit` / `cancel_order` events across the entire run-up.
Position retraced from +57% peak with the original entry-level stop
still in place — runner protection effectively unarmed.

**Counter-example (Grizzly, Apr 30 → May 1):** BTC LONG opened Apr 30
22:09 UTC. DSL ratchet ladder fired Tier 1 at +5%, Tier 3 at +15%, Tier
5 at +30% — telegram alerts confirm tier-transition events propagated
to the venue. Position closed via venue SL at $78,592 / +29.9% / +$76.36.
**Ratchet engine works on BTC main-DEX end-to-end.**

**Narrowed scope:** the Dire-specific issue is likely XYZ-DEX-only OR
runtime-restart-during-peak timing. NOT a fleet-wide ratchet bug.
Erik notified 2026-05-01.

**Status:** Awaiting Erik investigation on the narrower XYZ / restart-
timing path. **No per-skill patch.** Documented for cross-reference.

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
- 2026-05-01: FP-001 + FP-002 + FP-003 second adoption in Grizzly v5.8.0.
  Grizzly Apr 30 trade (+$76 / +29.9% via Tier 5 lock) narrowed FP-004
  scope from "fleet-wide" to "Dire / XYZ-DEX-specific or restart-timing-
  specific." BTC main-DEX ratchet path confirmed working end-to-end.
- 2026-05-01: FP-001 + FP-002 third adoption in Vulture v2.4.0. Vulture
  ZEC LONG (live, +22.8% margin ROE / +$117 unrealized; T0 lock fired
  at $347.17) is the second main-DEX agent confirming DSL ratchet works
  on small-cap perps. FP-003 (pattern-completeness gate) NOT applied to
  Vulture — its multi-mode signal architecture (Stalker + Striker) isn't
  a 5-confirmation pattern; forcing it would break the strategy.
- 2026-05-01: Vulture v3.0.0 — full v2-runtime-native rewrite. Producer
  + LLM-pass-through gate + native risk.guard_rails + DSL with
  FEE_OPTIMIZED_LIMIT exits + chain DB telemetry. v2.x's cfg.set_cooldown
  silent crash is structurally impossible in v3.0 (state owned by runtime,
  not Python). All scoring + DSL preset preserved from v2.4 (proved
  correct on the +$117 ZEC trade). FP-001 / FP-002 carried forward.
  This pattern is the template for migrating other v1-runtime agents
  (Polar, Kodiak, Wolverine, Grizzly, Bison, Python, Lemon, etc.) to
  v2 runtime.
- 2026-05-01: Vulture v3.0.1 — drop tier 5 (trigger_pct 150 invalid in
  v2 runtime; cap is 0 < trigger_pct <= 100). T4 (100/85) becomes apex.
- 2026-05-01: Vulture v3.0.2 — wallet read from config.json (no
  hardcoded env vars in cron). Per fleet rule against hardcoding wallet-
  specific values outside config files.
- 2026-05-01: Polar v4.0.0 — second v1→v2 migration (template proven on
  Vulture). v3.x scoring + Phase 2 ladder preserved exactly. All time-
  cuts remain disabled (v3.0.4/5/6 fixes carried forward). Wallet read
  from config.json (Vulture v3.0.2 pattern). Live ETH LONG (+$54
  unrealized at migration time) preserved via venue-side DSL stops
  during the runtime swap. Deployed cleanly: producer logging
  _polar_producer_version="4.0.0" with healthy gate output (e.g.
  "BLOCKED: sm_weak_1.9%").
- 2026-05-01: Wolverine v4.0.0 — third v1→v2 migration. v3.0.3 six-gate
  entry validation preserved EXACTLY (incl. the v3.0.3 4h-magnitude
  fix that rejects dead-flat HYPE chop — Wolverine's own self-
  diagnostic from 2026-04-23). All v3.0.1/2/4 v1-DSL fixes preserved
  (time-cuts disabled). Currently flat (no live position), so
  migration is risk-free.
