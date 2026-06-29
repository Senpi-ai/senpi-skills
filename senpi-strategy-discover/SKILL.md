


## Running the engine

The `exec` tool runs from the workspace (`/data/workspace`), **not** this skill directory, so a
relative `python3 scripts/discover.py …` fails with `No such file or directory`. **Always invoke the
script by its full path:**

```
python3 "$OPENCLAW_STATE_DIR/skills/senpi-strategy-discover/scripts/discover.py" [flags]
```

If `OPENCLAW_STATE_DIR` is unset, use the absolute path
`python3 /data/.openclaw/skills/senpi-strategy-discover/scripts/discover.py [flags]`.

## Install — include the MCP helper

The scripts in `scripts/` import a vendored MCP helper, `scripts/mcp_client.py`, at runtime.
**Install the whole `scripts/` directory** — omitting `mcp_client.py` fails with
`No module named 'mcp_client'`. Stdlib only, no other runtime dependencies.
