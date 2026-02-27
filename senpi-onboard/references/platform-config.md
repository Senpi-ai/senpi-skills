# MCP Server Platform Configuration

The Senpi MCP server is a hosted HTTP endpoint accessed via `mcp-remote`. Choose the configuration method based on the detected agent platform.

**Variables used below:** `API_KEY` comes from Step 4 (API response). `SENPI_MCP_ENDPOINT` comes from the defaults in the main skill (default: `https://mcp.prod.senpi.ai`).

## Detection Order

1. If `mcporter` CLI is available (`MCPORTER_AVAILABLE=true` from Step 0) -> Option A
2. If `claude` CLI is available -> Option B
3. Otherwise -> Option C

---

## Option A: OpenClaw (mcporter)

```bash
mcporter config add senpi --command npx \
  --persist ~/.openclaw/workspace/config/mcporter.json \
  --env SENPI_AUTH_TOKEN="${API_KEY}" \
  -- mcp-remote "${SENPI_MCP_ENDPOINT}/mcp" \
  --header "Authorization: Bearer \${SENPI_AUTH_TOKEN}"
```

If mcporter fails, fall back to Option C silently.

---

## Option B: Claude Code

```bash
claude mcp add senpi \
  --scope user \
  -e SENPI_AUTH_TOKEN="${API_KEY}" \
  -- npx mcp-remote "${SENPI_MCP_ENDPOINT}/mcp" \
  --header "Authorization: Bearer \${SENPI_AUTH_TOKEN}"
```

This registers the Senpi MCP server globally for the current user.

---

## Option C: Generic (.mcp.json)

Create or update the MCP configuration file. If the file already exists, **merge** -- do not overwrite existing server configurations.

```json
{
  "mcpServers": {
    "senpi": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "${SENPI_MCP_ENDPOINT}/mcp",
        "--header",
        "Authorization: Bearer ${SENPI_AUTH_TOKEN}"
      ],
      "env": {
        "SENPI_AUTH_TOKEN": "<API_KEY_VALUE>"
      }
    }
  }
}
```

Replace `<API_KEY_VALUE>` with the actual API key from Step 4.

**Merge rules:**
1. Read the current file content.
2. Parse as JSON.
3. Add or replace only the `senpi` key inside `mcpServers`.
4. Write back the full merged config.
5. Never delete other server configurations.
