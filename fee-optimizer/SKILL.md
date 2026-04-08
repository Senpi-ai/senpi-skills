---
name: fee-optimizer
description: >-
  When to use ALO (fee-optimized) vs MARKET orders on Hyperliquid via Senpi,
  and standard order params for entry/exit/TP. Use when configuring execution.
license: MIT
compatibility: >-
  Requires Senpi MCP (create_position, close_position, edit_position).
  Hyperliquid perp only.
metadata:
  author: senpi
  version: "1.0"
  platform: senpi
  exchange: hyperliquid
---

# Fee Optimizer

Use this skill when the user or another skill needs: (1) when to use fee-optimized (ALO) vs aggressive (MARKET) execution, or (2) standard order parameters for entry, exit, take-profit, or stop/emergency.

**When to use ALO:** Planned entries, take-profit exits, scaling in, margin edits. Use MARKET for stop losses, emergency exits, and time-sensitive momentum entries.

**References:**
- [references/alo-guide.md](references/alo-guide.md) — Full ALO guide (three modes, fee math, executionAsMaker).
- [references/order-params.md](references/order-params.md) — Canonical orderType and feeOptimizedLimitOptions per context.

**Scripts:**
- `scripts/get_order_spec.py` — Returns order spec (orderType + options) for a given context (entry, exit_tp, exit_sl, exit_emergency).

When you change this skill, bump `metadata.version` in this file so the skill-update checker can notify users; they apply updates with `npx skills update`.
