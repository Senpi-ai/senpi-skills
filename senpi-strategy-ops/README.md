# Running a Senpi strategy

Strategies live under [`strategies/<id>/`](../strategies) on the **`strategy-v2`** branch. To deploy
one, a tester gives an agent a one-line request:

> **Run `asia-ai` on https://github.com/Senpi-ai/senpi-skills/tree/strategy-v2 using $500 budget**

The agent maps that to a single command:

```bash
senpi-strategy-ops/scripts/run.sh asia-ai https://github.com/Senpi-ai/senpi-skills/tree/strategy-v2 500
# equivalently, with a bare branch name:
senpi-strategy-ops/scripts/run.sh asia-ai strategy-v2 500
```

`run.sh` parses the branch from the URL, checks it out, and runs the three deploy steps —
`create` (fund the wallet(s)) → `runtime` (start) → `verify` (confirm scanning).

## Host prerequisites
- `@senpi/runtime` **≥ 3.0.6** installed.
- `SENPI_AUTH_TOKEN` set (the deploying account's token).
- A funding source holding at least the strategy's `min_budget` (see its `strategy.yaml`).

## Manual / step-by-step
```bash
git checkout strategy-v2 && git pull
cd senpi-strategy-ops/scripts
python3 deploy.py create  <id> --budget <usd>   # create + fund wallet(s)
python3 deploy.py runtime <id>                  # start the runtime
python3 deploy.py verify  <id>                  # confirm scanners ticking
```

## What to expect
Strategies are **selective** — they emit only when their setup appears, so a freshly deployed
strategy often sits flat. **Flat is not broken.** `verify` confirms the scanners are *ticking*;
positions follow when a qualifying signal fires. Multi-wallet strategies (e.g. `asia-ai`) split the
budget across instances per each instance's `funding_share` in `strategy.yaml` (asia-ai: 65% main /
35% hedge).

## What's available
[`strategies/catalog.json`](../strategies/catalog.json) is the index of deployable strategies and
their metadata.
