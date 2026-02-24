#!/usr/bin/env python3
"""
HOWL Setup Wizard
Creates the daily retrospective cron job in OpenClaw.

Usage: python3 howl-setup.py

Interactive prompts:
  - Strategy wallet address
  - Telegram chat ID
  - Run time (default: 23:55 local)
  - Timezone (default: America/New_York)
"""

import json
import subprocess
import sys
import os

def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default

def main():
    print("=" * 50)
    print("🔍 HOWL — Hunt, Optimize, Win, Learn — Setup Wizard")
    print("=" * 50)
    print()

    wallet = ask("Strategy wallet address", "0x...")
    if not wallet or wallet == "0x...":
        print("❌ Wallet address required")
        sys.exit(1)

    telegram_id = ask("Telegram chat ID")
    if not telegram_id:
        print("❌ Telegram chat ID required")
        sys.exit(1)

    hour = ask("Run hour (0-23, local time)", "23")
    minute = ask("Run minute (0-59)", "55")
    tz = ask("Timezone", "America/New_York")

    cron_expr = f"{minute} {hour} * * *"

    # Build the analysis prompt reference
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(skill_dir, "references", "analysis-prompt.md")

    message = (
        f"You are the WOLF Strategy Retrospective Analyst. "
        f"Review the last 24 hours of autonomous trading and produce a structured HOWL report.\n\n"
        f"READ the full analysis prompt at: {prompt_path} — follow every step.\n\n"
        f"Key files:\n"
        f"- memory/YYYY-MM-DD.md (today's date + yesterday)\n"
        f"- MEMORY.md (long-term context)\n"
        f"- dsl-state-WOLF-*.json (all DSL state files)\n"
        f"- wolf-strategy.json (current config)\n"
        f"- skills/wolf-strategy/SKILL.md (current strategy skill)\n"
        f"- scripts/emerging-movers.py (scanner filters)\n\n"
        f"Wallet: {wallet}\n"
        f"Telegram: telegram:{telegram_id}\n\n"
        f"Use mcporter to query Senpi trade history. "
        f"Use message tool to send Telegram summary. "
        f"Save full report to memory/howl-YYYY-MM-DD.md. "
        f"Update MEMORY.md with distilled learnings.\n\n"
        f"Be brutally honest. Data-driven. No fluff."
    )

    job = {
        "name": "HOWL — Hunt, Optimize, Win, Learn",
        "schedule": {
            "kind": "cron",
            "expr": cron_expr,
            "tz": tz
        },
        "sessionTarget": "isolated",
        "payload": {
            "kind": "agentTurn",
            "message": message,
            "timeoutSeconds": 600
        },
        "delivery": {
            "mode": "announce"
        },
        "enabled": True
    }

    print()
    print("📋 Cron job configuration:")
    print(f"   Schedule: {cron_expr} ({tz})")
    print(f"   Wallet: {wallet}")
    print(f"   Telegram: {telegram_id}")
    print(f"   Timeout: 600s (10 min)")
    print()

    confirm = ask("Create cron job? (y/n)", "y")
    if confirm.lower() != "y":
        print("Cancelled.")
        sys.exit(0)

    # Output the job JSON for the user to paste into OpenClaw cron add
    # (or they can use the API directly)
    job_file = "/tmp/wolf-howl-cron.json"
    with open(job_file, "w") as f:
        json.dump(job, f, indent=2)

    print(f"\n✅ Cron job config saved to {job_file}")
    print()
    print("To create the cron job, tell your OpenClaw agent:")
    print(f'  "Create a cron job from {job_file}"')
    print()
    print("Or use the OpenClaw cron API directly with this payload:")
    print(json.dumps(job, indent=2))
    print()
    print("🎉 Setup complete! The HOWL will run daily and send you a Telegram summary.")

if __name__ == "__main__":
    main()
