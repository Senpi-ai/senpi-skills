# senpi-audit

A Senpi skill that answers **"what happened?"** — recent account activity, a single strategy's change
history, or failure investigation. The clean read-bucket for the audit tools.

Same hidden-engine pattern as the other read skills. Collapses: `audit_get_recent_actions`,
`audit_get_strategy_history`, `audit_query`.

## Modes

```sh
python3 scripts/audit.py                  # recent activity across the account
python3 scripts/audit.py --strategy <id>  # one strategy's full mutation history
python3 scripts/audit.py --failures       # only failed operations (debugging)
python3 scripts/audit.py --tool <name>    # filter to one tool
python3 scripts/audit.py --dry            # raw schema dump
python3 scripts/audit.py --fixture tests/fixtures/audit_fixture.json   # offline (tests)
```

Returns `{entries, summary, meta}` — each entry carries `time`, `action_type`, `tool`, `success`,
`resource`, and the agent's `reason` for the action. `summary` rolls up counts + surfaces failures.

USER-scoped token required (defaults to the authenticated user's log). Offline test 4/4.

> **Role in the context-reduction plan:** this is the **read** half of the DEFER bucket — a coherent,
> findable skill so "what happened?" doesn't require the 3 raw `audit_*` tools in the eager list. The
> *mutation* DEFER tools (cancel/pause/withdraw/transfer/stops-edit) stay first-class (they need the
> confirmation UX), so they are not collapsed here.
