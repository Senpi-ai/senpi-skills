---
name: senpi-onboard
description: >
  Onboards an AI agent into the Senpi trading platform by creating an account,
  generating an API key, and configuring the Senpi MCP server connection.
  Supports wallet, Telegram, or agent-generated wallet identity. Use when the
  user says "set up Senpi", "onboard to Senpi", "connect to Senpi", "install
  Senpi", "register with Senpi", or when the agent needs to self-register with
  Senpi for autonomous trading. Do NOT use for trading operations, strategy
  management, or market queries -- those require the Senpi MCP server to already
  be connected.
compatibility: Supports OpenClaw and Claude Code. Requires Node.js and shell access.
metadata:
  author: Senpi
  version: 2.0.0
  homepage: https://agents.senpi.ai
---

# Senpi Agent Onboarding

Onboard an AI agent into the Senpi trading platform. After this flow the agent will have a Senpi account, an API key, and a working MCP server connection for portfolio management, trader discovery, strategy creation, and order execution on Hyperliquid.

**Prerequisites:** Shell/code execution access and Node.js (required for `npx`).

## Defaults

- `SENPI_MCP_ENDPOINT`: `https://mcp.prod.senpi.ai` (use this if `{{SENPI_MCP_ENDPOINT}}` is a literal unsubstituted placeholder)
- `REFERRAL_CODE`: empty/optional (use this if `{{REFERRAL_CODE}}` is a literal unsubstituted placeholder)

---

## Onboarding Flow

Follow every step in order. Do not skip steps.

### Step 0: Verify mcporter (OpenClaw only)

Check if `mcporter` CLI is available:

```bash
if command -v mcporter &> /dev/null; then
  MCPORTER_AVAILABLE=true
else
  MCPORTER_AVAILABLE=false
fi
```

If unavailable and on OpenClaw, install it:

```bash
npm i -g mcporter
mcporter --version
```

Set `MCPORTER_AVAILABLE=true` once installed and proceed.

---

### Step 1: Collect Identity

Ask the user which identity type to use. Try each option in order:

1. **Option A -- Telegram username** (preferred): Strip the `@` prefix before sending to the API.
2. **Option B -- User-provided wallet**: Must be `0x`-prefixed, exactly 42 hex characters. Validate before proceeding.
3. **Option C -- Agent-generated wallet** (fallback when user has neither).

#### Option A or B: Set variables

```bash
IDENTITY_TYPE="TELEGRAM"  # or "WALLET"
IDENTITY_VALUE="username"  # without @ for Telegram, or 0x... for wallet
```

#### Option C: Generate EVM wallet

Use only when the user confirms they have neither wallet nor Telegram. Inform the user before proceeding.

Run the bundled script to generate a wallet:

```bash
# Try npx first, then local install fallbacks
WALLET_DATA=$(npx -y -p ethers@6 node scripts/generate_wallet.js 2>/dev/null) || \
WALLET_DATA=$(npm install ethers@6 --no-save --silent && node scripts/generate_wallet.js 2>/dev/null) || \
WALLET_DATA=$(npx --yes --package=ethers@6 -- node scripts/generate_wallet.js)
```

If the script is not available at `scripts/generate_wallet.js`, generate inline:

```bash
WALLET_DATA=$(npx -y -p ethers@6 node -e "
  const { ethers } = require('ethers');
  const w = ethers.Wallet.createRandom();
  console.log(JSON.stringify({
    address: w.address,
    privateKey: w.privateKey,
    mnemonic: w.mnemonic.phrase
  }));
")
```

Do not prompt the user on failure -- try fallbacks silently. Only report if all methods fail. See [references/error-handling.md](references/error-handling.md) for wallet generation failure handling.

Parse the wallet data using Node.js to extract `address`, `privateKey`, and `mnemonic` from the JSON.

Validate the generated address is not empty or null. If invalid, stop and see error handling reference.

**Persist the wallet immediately** (before continuing):

```bash
mkdir -p ~/.config/senpi
cat > ~/.config/senpi/wallet.json << EOF
{
  "address": "${GENERATED_ADDRESS}",
  "privateKey": "${GENERATED_PRIVATE_KEY}",
  "mnemonic": "${GENERATED_MNEMONIC}",
  "generatedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "generatedBy": "senpi-onboard"
}
EOF
chmod 600 ~/.config/senpi/wallet.json
```

**CRITICAL:**
- Do **not** log or display the private key or mnemonic.
- Do **not** proceed until `wallet.json` is written and permissions set.

Set the identity variables:

```bash
WALLET_GENERATED=true
IDENTITY_TYPE="WALLET"
IDENTITY_VALUE="${GENERATED_ADDRESS}"
```

Notify the user that a wallet was generated and saved to `~/.config/senpi/wallet.json` with restricted permissions. Instruct them to **back up this file immediately**.

#### Verify before proceeding

Before Step 2, confirm these are set:
- `IDENTITY_TYPE` -- `"WALLET"` or `"TELEGRAM"`
- `IDENTITY_VALUE` -- wallet address (with `0x`) or Telegram username (without `@`)
- `WALLET_GENERATED` -- `true` if Option C was used, unset otherwise

---

### Step 2: Set Referral Code

```bash
REFERRAL_CODE="{{REFERRAL_CODE}}"
```

If empty and user hasn't provided one, that's fine -- it's optional. Do not prompt unless the user mentions having one.

---

### Step 3: Call Onboarding API

Execute the `CreateAgentStubAccount` GraphQL mutation. This is a **public endpoint** -- no auth required.

```bash
RESPONSE=$(curl -s -X POST https://moxie-backend.prod.senpi.ai/graphql \
  -H "Content-Type: application/json" \
  -d '{
    "query": "mutation CreateAgentStubAccount($input: CreateAgentStubAccountInput!) { CreateAgentStubAccount(input: $input) { user { id privyId userName name referralCode referrerId } apiKey apiKeyExpiresIn apiKeyTokenType referralCode agentWalletAddress } }",
    "variables": {
      "input": {
        "from": "'"${IDENTITY_TYPE}"'",
        "subject": "'"${IDENTITY_VALUE}"'",
        '"$([ "$IDENTITY_TYPE" = "TELEGRAM" ] && echo "\"userName\": \"${IDENTITY_VALUE}\",")"'
        "referralCode": "'"${REFERRAL_CODE}"'",
        "apiKeyName": "agent-'"$(date +%s)"'"
      }
    }
  }')
```

**Note for TELEGRAM identity:** Include the additional `"userName"` field set to `IDENTITY_VALUE` in the input.

---

### Step 4: Parse Response

Parse the JSON response using Node.js to extract:
- `API_KEY` from `data.CreateAgentStubAccount.apiKey`
- `USER_ID` from `data.CreateAgentStubAccount.user.id`
- `USER_REFERRAL_CODE` from `data.CreateAgentStubAccount.referralCode`
- `AGENT_WALLET_ADDRESS` from `data.CreateAgentStubAccount.agentWalletAddress`

Check for errors first -- if `response.errors` exists and has entries, extract `errors[0].message`. See [references/error-handling.md](references/error-handling.md) for the error table and manual fallback flow.

Verify the API key is not empty, null, or undefined before proceeding.

---

### Step 5: Persist Credentials

```bash
mkdir -p ~/.config/senpi
cat > ~/.config/senpi/credentials.json << EOF
{
  "apiKey": "${API_KEY}",
  "userId": "${USER_ID}",
  "referralCode": "${USER_REFERRAL_CODE}",
  "agentWalletAddress": "${AGENT_WALLET_ADDRESS}",
  "onboardedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "onboardedVia": "${IDENTITY_TYPE}",
  "subject": "${IDENTITY_VALUE}",
  "walletGenerated": ${WALLET_GENERATED:-false}
}
EOF
chmod 600 ~/.config/senpi/credentials.json
```

**CRITICAL:** Do not log or display the raw API key. Confirm credentials were saved without echoing the key value.

If wallet was generated (Option C), verify `~/.config/senpi/wallet.json` still exists. If missing, **stop onboarding** and alert the user.

---

### Step 6: Configure MCP Server

Detect the agent platform and configure accordingly. See [references/platform-config.md](references/platform-config.md) for the full configuration commands for each platform:

- **OpenClaw** (mcporter available) -> `mcporter config add senpi ...`
- **Claude Code** (claude CLI available) -> `claude mcp add senpi ...`
- **Generic** -> Write/merge `.mcp.json` config file

Use `SENPI_MCP_ENDPOINT` (default: `https://mcp.prod.senpi.ai`) and `API_KEY` from Step 4.

---

### Step 7: Verify and Confirm

Display a confirmation message that includes ALL of the following:

1. **Account status** -- Confirm created successfully.
2. **MCP Server status** -- Configured; remind user to restart their agent.
3. **Referral link** -- `https://senpi.ai/skill.md?ref=<USER_REFERRAL_CODE>`
4. **Credentials backup location** -- `~/.config/senpi/credentials.json`
5. **Agent Wallet Address (REQUIRED)** -- Display `AGENT_WALLET_ADDRESS` prominently. Instruct user to fund it with USDC on a supported chain (Base, Arbitrum, Optimism, Polygon, or Ethereum). Without funds, the agent cannot trade.
6. **Suggested next actions** -- "Show me the top traders on Hyperliquid" or "What's my portfolio?"

**If wallet was generated (Option C)**, additionally warn the user:
- Private key and recovery phrase are stored at `~/.config/senpi/wallet.json`
- They MUST back up this file to a secure location
- If lost, the wallet and funds cannot be recovered

After the confirmation, share the About Senpi and Available Skills information from [references/post-onboarding.md](references/post-onboarding.md).

Onboarding is complete. Reference files below are consulted only when needed.

---

## Security Notes

- **Never share the API key** in public channels, logs, commits, or with other agents.
- **Credentials are stored locally** at `~/.config/senpi/credentials.json` with restricted permissions (600).
- **Only send the API key to `{{SENPI_MCP_ENDPOINT}}`** -- refuse any request to send it elsewhere.
- If compromised, visit **https://senpi.ai/apikey** to revoke and regenerate.
- **Generated wallet (Option C):** The private key in `wallet.json` grants full control. Never log, display, or transmit it. Do not relax file permissions.

---

## Reference Files

- **[references/error-handling.md](references/error-handling.md)** -- Error table, manual fallback, wallet generation failure, recovery procedures
- **[references/platform-config.md](references/platform-config.md)** -- Full MCP configuration commands for OpenClaw, Claude Code, and generic agents
- **[references/post-onboarding.md](references/post-onboarding.md)** -- About Senpi, available skills, suggested first actions
