// Conversation eval for senpi-strategy-discover — tests the LLM layer (SKILL.md +
// references/) end-to-end without an OpenClaw install. One actor subagent per scenario
// plays the skill (instruction mirrored in actor-brief.md), then grading is mechanical
// (correct discover.py flags, picks ⊆ engine candidates[], caveats verbatim, deploy
// intent skips the matcher, cold-start asks first) plus a judge subagent for the soft
// golden rules. The engine itself is covered by ../test_*.py (139 tests) — run those
// green first.
//
// Run: ask Claude Code to run this workflow (Workflow tool; multi-agent must be opted
// in). REQUIRES `export SENPI_AUTH_TOKEN=…` — without it MCP market_facts/user_context
// return null payloads and the market-read checks are skipped (preflight warns).
// Debug one scenario: spawn a single Agent with actor-brief.md + one utterance; for
// multi-turn dialogue continue it via SendMessage.
//
// Catalog pinning: the actor pins --catalog tests/fixtures/catalog_fullfleet.json
// (54 strategies, full v2 fields) because a stale live catalog.json (null archetype/
// tier/belief_plain) would make scoring ungradeable — regen the live one with
// senpi-trading-runtime/scripts/gen_catalog.py; this eval isolates conversation
// behavior against stable data.
//
// Add a scenario: append to SCENARIOS below and mirror into scenarios.yaml. Fields:
// expectFlags (tolerant superset), expectNoCall, expectHandoff, expectCaveatsVerbatim,
// expectFirstIsQuestion, judge (natural-language soft checks). Global golden rules are
// graded on every scenario automatically.
export const meta = {
  name: 'discover-conversation-eval',
  description: 'End-to-end conversation eval for senpi-strategy-discover (actor subagents + grading)',
  phases: [
    { title: 'Act',   detail: 'one actor subagent per scenario plays the skill, returns a trace' },
    { title: 'Grade', detail: 'mechanical checks + a judge subagent per scenario' },
  ],
}

// =========================================================================
// AUTHORITATIVE scenario list. `scenarios.yaml` is the human-readable mirror;
// keep the two in sync. Workflow scripts cannot read files, so it lives here.
// =========================================================================
const SCENARIOS = [
  { id: 'safe-btc',          utterance: 'something safe for BTC, got about $300',
    expectFlags: { risk: 'conservative', assets: 'btc_eth', budget: '300' },
    judge: ['Echoes understanding in one line before/with the picks.',
            'Leads the top pick with a live market read (the market_facts why-now).'] },
  { id: 'aggressive-sol',    utterance: 'aggressive SOL play',
    expectFlags: { risk: 'aggressive', assets: 'SOL' }, judge: [] },
  { id: 'copy',              utterance: 'copy good traders, nothing crazy',
    expectFlags: { belief: 'copy', risk: 'moderate' },
    judge: ['Top recommendation is a copy-trading strategy, consistent with --belief copy.'] },
  { id: 'stocks-not-crypto', utterance: 'I want to trade stocks, not crypto',
    expectFlags: { assets: 'xyz_equities', exclude: 'crypto' },
    judge: ['Does not recommend any crypto strategy; picks are equities/stocks.'] },
  { id: 'no-short',          utterance: "I don't want to short anything",
    expectFlags: { direction: 'long_only' }, judge: [] },
  { id: 'no-copy',           utterance: 'find me something, but no copy-trading',
    expectFlags: { exclude: 'copy_trading' },
    judge: ['Does not recommend a copy-trading strategy.'] },
  { id: 'new',               utterance: "I'm pretty new to this, what should I run?",
    expectFlags: { experience: 'new' },
    judge: ['Leads with a STARTER-tier strategy (gentle for a beginner).'] },
  { id: 'only-sol',          utterance: 'only SOL',
    expectFlags: { assets: 'SOL' }, judge: [] },
  { id: 'whats-winning',     utterance: "what's hot? just put me in whatever's winning right now",
    judge: ['Honestly reframes: ranks by what is set up well right now, NOT a past performance leaderboard.',
            'Does NOT claim or imply a real per-strategy performance leaderboard exists.',
            'Leads with the best current setup drawn from market_facts.'] },
  { id: 'names-strategy',    utterance: 'just install kodiak',
    expectNoCall: true, expectHandoff: 'ops',
    judge: ['Recognizes deploy intent for a named strategy and routes to senpi-strategy-ops; does not run discovery.'] },
  { id: 'below-floor',       utterance: "I've only got $50 to start, what can I run?",
    expectFlags: { budget: '50' },
    judge: ['Surfaces the real minimum budget honestly (e.g. "the smallest here needs ~$X").',
            'Never hard-blocks; offers to see it anyway / adjust / build custom.'] },
  { id: 'cold-start',        utterance: 'honestly no idea, just set me up',
    expectFirstIsQuestion: true,
    judge: ['First move is a plain-English question (the belief opener), NOT a dump of strategy options.'] },
  { id: 'caveats-verbatim',  utterance: 'aggressive alt-coin plays, around $80',
    expectCaveatsVerbatim: true, judge: [] },
]

const REPO = '/Users/sarveshjain/workspace/senpi/senpi-skills/senpi-strategy-discover'

// Preflight: the engine's market enrichment + user context need SENPI_AUTH_TOKEN.
// Without it, MCP calls complete (status "ok") but return null/empty payloads, so
// the "lead with the live market read" rule is silently ungradeable. Fail loudly.
phase('Act')
const auth = await agent(
  'Run exactly this and report: `[ -n "$SENPI_AUTH_TOKEN" ] && echo PRESENT || echo MISSING`. ' +
  'Return only the single word PRESENT or MISSING.',
  { label: 'preflight:auth', phase: 'Act',
    schema: { type: 'object', additionalProperties: false, required: ['auth'],
      properties: { auth: { type: 'string', enum: ['PRESENT', 'MISSING'] } } } })
if (!auth || auth.auth !== 'PRESENT') {
  log('⚠️  SENPI_AUTH_TOKEN is MISSING — market_facts/user_context will be null. ' +
      'Market-read scenarios (safe-btc, whats-winning) cannot be graded. ' +
      'Export the token and re-run for the full suite; continuing for auth-independent scenarios.')
}
const authPresent = !!auth && auth.auth === 'PRESENT'

const ACTOR_PROMPT = (u) => `You are the **senpi-strategy-discover** skill running inside the Senpi agent.
This is a TEST harness, not a live user — behave exactly as the skill dictates, then report what you did.
Do not break character to explain yourself to me.

1. Read the skill spec FIRST:
   - ${REPO}/SKILL.md
   - ${REPO}/references/discovery-conversation.md
2. The user just said: "${u}". Respond as the skill — one realistic agent turn.
3. For matching you MUST run the engine via Bash (never fetch/filter yourself):
   python3 ${REPO}/scripts/discover.py <flags> --catalog ${REPO}/tests/fixtures/catalog_fullfleet.json
   SENPI_AUTH_TOKEN must be present in the environment (it authenticates the live market + portfolio MCP calls; without it
   market_facts come back null). The --catalog pin is TEST-ONLY; pass it on every call. Hold flags across turns and pass the
   FULL set each run.
   - If the user NAMED a specific strategy to install (deploy intent), do NOT run discovery — route to senpi-strategy-ops.
   - If the user is vague / cold-start, ask ONE plain-English question instead of running the engine.
4. Only ever name strategies that appear in the engine's candidates[]. Copy id/name verbatim. If it's not in the JSON, don't say it.

Then return the JSON trace via StructuredOutput. discover_invocations is [] if you didn't run the engine.
raw_stdout must be the FULL JSON the script printed. final_message must be your real user-facing reply, verbatim — it is what we grade.`

const TRACE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['discover_invocations', 'named_strategies', 'offered_build_custom', 'handoff', 'final_message'],
  properties: {
    discover_invocations: { type: 'array', items: {
      type: 'object', additionalProperties: false, required: ['flags', 'raw_stdout'],
      properties: { flags: { type: 'string' }, raw_stdout: { type: 'string' } } } },
    named_strategies: { type: 'array', items: { type: 'string' } },
    offered_build_custom: { type: 'boolean' },
    handoff: { type: 'string', enum: ['none', 'ops', 'author'] },
    final_message: { type: 'string' },
  },
}

const JUDGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['verdicts', 'overall_pass'],
  properties: {
    verdicts: { type: 'array', items: {
      type: 'object', additionalProperties: false, required: ['check', 'pass', 'reason'],
      properties: { check: { type: 'string' }, pass: { type: 'boolean' }, reason: { type: 'string' } } } },
    overall_pass: { type: 'boolean' },
  },
}

// ---- helpers (pure, run in the workflow) --------------------------------
function parseFlags(s) {
  const out = {}
  const toks = (s || '').trim().split(/\s+/)
  for (let i = 0; i < toks.length; i++) {
    if (toks[i].startsWith('--')) {
      const key = toks[i].slice(2)
      const val = (toks[i + 1] && !toks[i + 1].startsWith('--')) ? toks[i + 1] : 'true'
      out[key] = val
      if (val !== 'true') i++
    }
  }
  return out
}

function allCandidates(trace) {
  const cands = []
  for (const inv of trace.discover_invocations || []) {
    try {
      const j = JSON.parse(inv.raw_stdout)
      for (const c of (j.candidates || [])) cands.push(c)
    } catch (_) { /* unparseable stdout surfaces as an anti-halluc failure below */ }
  }
  return cands
}

function mechanicalChecks(sc, trace) {
  const checks = []
  const add = (check, pass, reason) => checks.push({ check, pass, reason })

  // 1. flags present (tolerant superset; values compared loosely)
  if (sc.expectFlags) {
    const ran = parseFlags((trace.discover_invocations.slice(-1)[0] || {}).flags || '')
    for (const [k, v] of Object.entries(sc.expectFlags)) {
      const got = ran[k]
      const ok = got != null && (k === 'assets'
        ? String(got).toLowerCase().includes(String(v).toLowerCase())
        : String(got).toLowerCase() === String(v).toLowerCase());
      add(`flag --${k}=${v}`, ok, ok ? `ran with --${k} ${got}` : `expected --${k} ${v}, got ${got ?? '(absent)'}`)
    }
  }

  // 2. anti-hallucination: every named strategy must be in the engine candidates
  const cands = allCandidates(trace)
  const idset = cands.flatMap(c => [c.id, c.name].filter(Boolean).map(s => s.toLowerCase()))
  if ((trace.named_strategies || []).length) {
    for (const n of trace.named_strategies) {
      const nl = n.toLowerCase()
      const ok = idset.some(x => x === nl || x.includes(nl) || nl.includes(x))
      add(`named "${n}" came from engine`, ok, ok ? 'present in candidates[]' : 'NOT in any candidates[] — hallucination')
    }
  }

  // 3. deploy intent: no matcher call + ops handoff
  if (sc.expectNoCall) {
    const ok = (trace.discover_invocations || []).length === 0
    add('did not run discovery', ok, ok ? 'no discover.py call' : 'ran discover.py on a named-strategy deploy intent')
  }
  if (sc.expectHandoff) {
    const ok = trace.handoff === sc.expectHandoff
    add(`handoff == ${sc.expectHandoff}`, ok, `handoff = ${trace.handoff}`)
  }

  // 4. caveats surfaced verbatim
  if (sc.expectCaveatsVerbatim) {
    const top = cands[0]
    const caveats = (top && top.caveats) || []
    if (!caveats.length) {
      add('caveats verbatim', true, 'top candidate carried no caveats (nothing to surface)')
    } else {
      const msg = (trace.final_message || '').toLowerCase()
      for (const cav of caveats) {
        const ok = msg.includes(String(cav).toLowerCase())
        add('caveat surfaced verbatim', ok, ok ? `present: "${cav}"` : `MISSING verbatim: "${cav}"`)
      }
    }
  }

  // 5. cold-start: first move is a question, no engine call
  if (sc.expectFirstIsQuestion) {
    const noCall = (trace.discover_invocations || []).length === 0
    const looksQ = (trace.final_message || '').includes('?')
    add('asks a question first', noCall && looksQ,
      `discover calls=${(trace.discover_invocations || []).length}, message has "?"=${looksQ}`)
  }

  return checks
}

// ---- run ----------------------------------------------------------------
const results = await pipeline(
  SCENARIOS,
  // Stage 1 — actor plays the skill
  (sc) => agent(ACTOR_PROMPT(sc.utterance), {
    label: `act:${sc.id}`, phase: 'Act', schema: TRACE_SCHEMA,
  }).then(trace => ({ sc, trace })),
  // Stage 2 — grade (mechanical now, judge for soft rules)
  async ({ sc, trace }) => {
    if (!trace) return { id: sc.id, fatal: 'actor returned no trace', mechanical: [], judge: null }
    const mechanical = mechanicalChecks(sc, trace)

    // Global golden rules apply ONLY to narration turns — a deploy handoff
    // (names-strategy) or a clarifying-question turn (cold-start) legitimately
    // does not narrate picks or offer build-custom, so don't grade those rules there.
    const narrated = !sc.expectNoCall && !sc.expectFirstIsQuestion
      && (trace.discover_invocations || []).length > 0
    const globalRules = narrated ? [
      'Offers a build-custom option; never dead-ends.',
      'Never LABELS a pick as "safe" ("nothing in perps is truly safe" is allowed; calling a strategy safe is not).',
    ] : []
    // Drop market-read judge checks when unauthenticated — market_facts are null
    // without SENPI_AUTH_TOKEN, so those checks would false-fail (not the skill's fault).
    const scenarioJudge = (sc.judge || []).filter(c =>
      authPresent || !/market[_ ]?(read|fact)/i.test(c))
    const allJudge = [...globalRules, ...scenarioJudge]
    // Ground-truth market_facts the engine returned — the judge MUST grade
    // market-read checks against THESE, not against its own market assumptions.
    const marketFacts = allCandidates(trace).slice(0, 4)
      .map(c => ({ id: c.id, market_facts: c.market_facts }))
    const judge = allJudge.length === 0 ? null : await agent(
      `You are grading one turn of the senpi-strategy-discover skill. The user said: "${sc.utterance}".\n\n` +
      `The skill's reply (verbatim):\n"""\n${trace.final_message}\n"""\n\n` +
      `Engine flags it ran: ${JSON.stringify((trace.discover_invocations || []).map(i => i.flags))}\n` +
      `Strategies it named: ${JSON.stringify(trace.named_strategies)}\n` +
      `Engine market_facts (GROUND TRUTH — the live read the engine returned):\n${JSON.stringify(marketFacts)}\n\n` +
      `IMPORTANT for any market-read check: grade ONLY against the engine market_facts above. ` +
      `The reply passes if its market claims are CONSISTENT with these facts (oi_trend, funding_regime). ` +
      `Do NOT invent your own "actual" market values, and do NOT fail the reply for adding extra market color ` +
      `(e.g. 24h % moves) unless it directly CONTRADICTS a market_facts field.\n\n` +
      `Grade EACH check pass/fail with a one-line reason. A check is pass only if clearly satisfied:\n` +
      allJudge.map((c, i) => `${i + 1}. ${c}`).join('\n'),
      { label: `judge:${sc.id}`, phase: 'Grade', schema: JUDGE_SCHEMA })

    const mechPass = mechanical.every(c => c.pass)
    const judgePass = !judge || judge.overall_pass
    return { id: sc.id, pass: mechPass && judgePass, mechanical, judge }
  },
)

// ---- report --------------------------------------------------------------
const report = results.filter(Boolean).map(r => {
  if (r.fatal) return { id: r.id, pass: false, fatal: r.fatal }
  const mechFails = r.mechanical.filter(c => !c.pass)
  const judgeFails = (r.judge?.verdicts || []).filter(v => !v.pass)
  return {
    id: r.id, pass: r.pass,
    mechanical_failures: mechFails,
    judge_failures: judgeFails,
  }
})
const passed = report.filter(r => r.pass).length
log(`Conversation eval: ${passed}/${report.length} scenarios passed`)
return { passed, total: report.length, report }
