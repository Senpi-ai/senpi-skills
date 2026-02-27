# Error Handling Reference

## Missing Node.js

If `node` or `npx` is not found, all skill installation commands will fail. Instruct the user to install Node.js:

- macOS: `brew install node`
- Linux: `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs`
- Or visit: https://nodejs.org

After installation, verify with `node --version` and retry from Step 1.

## Skill Installation Failure

If `npx skills add` fails for reasons other than missing Node.js:

1. Check network connectivity.
2. Retry once.
3. If it persists, install the skill manually by downloading the raw SKILL.md:
   ```bash
   curl -sL https://raw.githubusercontent.com/Senpi-ai/senpi-skills/main/<skill-name>/SKILL.md -o SKILL.md
   ```
   Load the downloaded file and follow its instructions.

## MCP Server Configuration Issues

If the MCP server is configured but tool calls fail:

1. Verify the API key is set and not expired.
2. Confirm `SENPI_MCP_ENDPOINT` resolves to `https://mcp.prod.senpi.ai` (or the intended endpoint).
3. Test connectivity with a simple tool call (e.g., `account_get_portfolio`).
4. If the server was just configured, restart the agent's MCP client to pick up the new configuration.
5. If issues persist, re-run the `senpi-onboard` skill to regenerate credentials.
