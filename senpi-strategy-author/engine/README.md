# The build engine — reference for the chat agent

`scripts/author_build.py` runs a headless Claude Code session on this host that writes a strategy
package, lints it and loops on `openclaw senpi validate` until it passes. SKILL.md holds the flow;
this file holds the detail.

## The spec you write before `start`

`/data/workspace/.author-specs/<id>.json`, in the user's words — not a summary of them. The engine
refuses a spec without `user_confirmed: true`, which is true only because the user said yes to your
replay.

```json
{"id": "<slug>", "name": "<their name>", "thesis": "<one paragraph>", "user_confirmed": true,
 "decisions": {"universe": "…", "data": "…", "edge": "…", "shape": "…", "cardinality": "…",
               "memory": "…", "exit_risk": "<preset + guard rails + leverage + cadence>"},
 "opening_constraints": ["every constraint from their first message"],
 "exit_preview": "<the ladder you replayed, in price terms>"}
```

A contradiction inside the spec is not yours to resolve silently: the engine returns `needs_input`
and you ask. A number the user gave is copied exactly — a cadence transcribed as an hour when they
said 15 minutes is the failure this file exists to prevent.

## Editing, and what "resume" means

`start --edit <pkg-dir>` builds on a **staged copy**; the live package is untouched until
`promote --job <job>`, which refuses unless the staged copy carries a valid proof, and keeps a
`.bak-<timestamp>` of the original.

Each finished build writes `.author-build.json` into the package: the job id, the Claude Code
session id, and `key_choices`. An edit **resumes that session** when the strategy id matches and
Claude Code still has it (`status` shows `resumed_from`), so the model keeps its own reasoning and
re-reads far less. It starts fresh when the id differs (a fork carries the original's marker), when
the session is gone, or when there is no marker. Either way the package on disk is the truth.

A package that is already LIVE is a different conversation: apply it with `senpi-strategy-ops`
(`openclaw senpi update`), and tell the user that `dsl_preset` is forward-only.

## Answering questions later

The package is self-describing: `runtime.yaml`'s `description` is the mandate, and
`.author-build.json` holds `key_choices` and the summary. `status --job <job> --full` has the whole
result, and `/data/workspace/.author-jobs/<job>/events.jsonl` is the build's own log. Read those
rather than inferring intent from the code.

## What the engine may and may not do

Read-only Senpi MCP (market, discovery, leaderboard, account, `strategy_get*`/`strategy_list`, and
the guides); writes only inside the package and its job scratch dir. A guard hook blocks deploying,
closing, funding, network access, installs, cron and credential reads — and the session's deny list
removes every mutating Senpi tool from its tool list. Going live stays with `senpi-strategy-ops`,
after the user names a budget.

## When it is not available

`doctor` reports each check: `claude_cli` (image without Claude Code), `model_credential`
(no `AI_API_KEY`), `validate_cmd` (no `openclaw` on PATH), `engine_enabled`
(`SENPI_AUTHOR_ENGINE=inline`). Any false → build inline, SKILL.md stages 2–9.
