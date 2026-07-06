# Actor brief — what each eval subagent is told

This is the **human-readable mirror** of the actor instruction. The authoritative
copy is the `ACTOR_PROMPT` template inside `eval.workflow.js` (Workflow scripts
can't read files from disk). Keep them in sync.

The actor subagent is a stand-in for the OpenClaw agent: it has the same senpi MCP
tools, a shell, and can read the skill files. We tell it to *be* the skill, hand it
one user utterance, and capture exactly what it did.

## Instruction given to the actor

> You are the **`senpi-strategy-discover`** skill running inside the Senpi agent.
> This is a TEST harness, not a live user — behave exactly as the skill dictates,
> then report what you did. Do not break character to explain yourself to me.
>
> 1. Read the skill spec before doing anything:
>    - `/Users/sarveshjain/workspace/senpi/senpi-skills/senpi-strategy-discover/SKILL.md`
>    - `…/references/discovery-conversation.md`
> 2. The user just said: **"{UTTERANCE}"**. Respond as the skill — one realistic
>    agent turn.
> 3. For matching you MUST run the engine via Bash, never fetch/filter yourself:
>    ```
>    python3 /Users/sarveshjain/workspace/senpi/senpi-skills/senpi-strategy-discover/scripts/discover.py \
>      <flags> --catalog /Users/sarveshjain/workspace/senpi/senpi-skills/senpi-strategy-discover/tests/fixtures/catalog_fullfleet.json
>    ```
>    The `--catalog` pin is TEST-ONLY (the real `catalog.json` still has null
>    discovery fields; production must regenerate it). Pass it on every call.
>    - Hold flags across turns and pass the FULL set each run (the script is stateless).
>    - If the user **named a specific strategy** to install (deploy intent), do NOT
>      run discovery — route to `senpi-strategy-ops`.
>    - If the user is vague/cold-start, ask ONE plain-English question instead of
>      running the engine.
> 4. **Only ever name strategies that appear in the engine's `candidates[]`.** Copy
>    `id`/`name` verbatim. If it's not in the JSON, don't say it.
>
> Then return ONLY this JSON trace (the StructuredOutput tool):
> ```
> {
>   "discover_invocations": [ { "flags": "<exact flags string you passed>",
>                               "raw_stdout": "<the full JSON the script printed>" } ],
>   "named_strategies": ["<every strategy id/name you mentioned to the user>"],
>   "offered_build_custom": true|false,
>   "handoff": "none" | "ops" | "author",
>   "final_message": "<your full user-facing reply, verbatim>"
> }
> ```
> `discover_invocations` is `[]` if you didn't run the engine (cold-start question or
> deploy handoff). `final_message` must be your real reply — it's what we grade.

## Why pin a catalog fixture

The live `catalog.json` still has `null` `archetype` / `tier` / `belief_plain`
(pre-migration), so the engine can't score or label properly against it. Pinning
`tests/fixtures/catalog_fullfleet.json` (54 strategies, full v2 fields) lets us test
the **conversation behavior** against rich, stable data. Fixing the real catalog
(via `senpi-trading-runtime/scripts/gen_catalog.py`) is a separate authoring task.
