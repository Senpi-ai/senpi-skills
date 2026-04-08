# Telegram Identity Reference

> **Note:** These instructions are only needed as a fallback when `${OPENCLAW_WORKSPACE_DIR}/USER.md` is unavailable or missing the `## Telegram` section. In normal OpenClaw usage, the Telegram user ID and username are read automatically.

## Finding Your Numeric Telegram User ID

Present these instructions to the user before prompting for input:

> **To find your Telegram user ID:**
> 1. Open Telegram and search for **@userinfobot**
> 2. Start a chat and send any message (e.g. `/start`)
> 3. It will reply instantly with your numeric user ID (e.g. `8362644815`)
>
> Please enter your numeric Telegram user ID:

## Validation

`IDENTITY_VALUE` must contain only digits. If it contains any non-digit characters (letters, `@`, spaces, etc.), reject it and re-prompt with the instructions above.

For the Telegram username, accept input with or without a leading `@`, but always normalize before API use by removing the leading `@`.

```bash
IDENTITY_TYPE="TELEGRAM"
IDENTITY_VALUE="8362644815"   # numeric Telegram user ID (digits only)
RAW_TELEGRAM_USERNAME="@username"     # user input may include leading "@"
TELEGRAM_USERNAME="${RAW_TELEGRAM_USERNAME#@}" # normalized for API: "username"
```
