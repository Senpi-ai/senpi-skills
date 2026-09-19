# End-to-end test: Claude Code build engine on a live senpi-agent box

Branches: senpi-agent `feat/claude-code-author-engine` (image), senpi-skills
`feat/author-claude-code-engine` (author skill 3.12.0).

## 1. Put the box on the branch image

Image: `public.ecr.aws/n9x7j0j5/senpi-hyperclaw:development-<senpi-agent commit sha>`
(the build workflow's tag). Swap the service source image, then trigger a deploy — the swap alone
does not redeploy (senpi-railway-integration `UpdateServiceImage`).

## 2. Install the author skill from the branch

In chat: *"install the senpi-strategy-author skill from branch feat/author-claude-code-engine"*.
Or set `SENPI_SKILLS_BRANCH_SENPI_STRATEGY_AUTHOR=feat/author-claude-code-engine` on the service
(per-skill override; every other skill stays on main).

Check on the box (`railway ssh`):

```
grep '^  version' /data/.openclaw/skills/senpi-strategy-author/SKILL.md     # "3.12.0"
claude --version                                                            # 2.1.278
python3 /data/.openclaw/skills/senpi-strategy-author/scripts/author_build.py doctor
```

`doctor` must print `"ready": true`. Each false check names what is missing: `claude_cli` (wrong
image), `model_credential` (no `AI_API_KEY`), `validate_cmd` (no `openclaw` on PATH).

## 3. Prove the model route before a real build

```
cd /tmp && ANTHROPIC_BASE_URL=https://models.senpi.ai ANTHROPIC_AUTH_TOKEN="$AI_API_KEY" \
  claude -p "reply with the word ok" --model claude-opus-4-8 --tools "" --setting-sources "" \
  --strict-mcp-config --output-format json < /dev/null | head -c 400
```

A 401/403 here means the box's LiteLLM key is not allowed `claude-opus-4-8` — grant it on the proxy,
or set `SENPI_AUTHOR_MODEL` to a model the key can use.

## 4. The conversation

In chat, ask for a strategy from scratch, e.g. *"build me a strategy that goes long BTC when the 4h
and daily trends are both up, let winners run, 3x"*. Expected:

1. The agent runs the interview and replays the spec; say yes.
2. It runs `author_build.py doctor`, writes `/data/workspace/.author-specs/<id>.json`, execs `start`.
3. When the exec exits, the agent receives `AUTHOR_BUILD {...}` and reads `status`.
4. `done` → it relays `✓ static ✓ import ✓ live` and asks for a budget. `needs_input` → it asks the
   question; answer it and the same session resumes.

Evidence on the box:

```
python3 /data/.openclaw/skills/senpi-strategy-author/scripts/author_build.py list
python3 .../author_build.py status --job <job> --full
python3 .../author_build.py verify-proof /data/workspace/strategies/<id>
ls /data/workspace/.author-jobs/<job>/            # events.jsonl, claude.stderr.log, result.json
```

## 5. Deploy (optional, real money)

Only with an explicit budget: the agent hands the package to senpi-strategy-ops
(`deploy.py create /data/workspace/strategies/<id> --budget 12`) and waits for `overall: live`.
The engine itself can never deploy — step 4 moves no money.
