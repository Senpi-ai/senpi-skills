#!/usr/bin/env python3
"""PreToolUse guard for the Claude Code build engine (author_build.py).

Claude Code runs on the agent host with the user's SENPI_AUTH_TOKEN in its environment, and that
token can move money. Authoring needs none of that. This hook blocks, before the tool runs:

  - Senpi MCP tools that are not reads (allow-by-read-prefix, fail closed on anything unknown);
  - writes outside the package directory and the job directory;
  - Bash that deploys, closes, funds, installs software, reaches the network directly, schedules
    agent turns, or reads secrets.

Exit 2 blocks the call and hands stderr to the model as the reason. This is defense in depth on top
of the session's permission deny list — not a sandbox: it screens command text, so the rules name
what authoring never needs rather than trying to parse all of shell.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import json
import os
import re
import shlex
import sys
from pathlib import Path

# Reads, mirroring the runtime scaffold's read set (senpi_mcp.py). Anything else is refused.
READ_PREFIXES = ("market_", "discovery_", "leaderboard_", "account_get", "strategy_get", "execution_get",
                 "execution_estimate", "estimate_", "user_get", "arena_", "audit_", "get_")
READ_EXACT = {"strategy_list", "list_senpi_guides", "read_senpi_guide", "ratchet_stop_get",
              "ratchet_stop_list", "ratchet_stop_events"}
MUTATING = {
    "create_position", "close_position", "edit_position", "cancel_order", "send_usdc",
    "transfer_spot_to_perps", "strategy_create", "strategy_create_custom_strategy", "strategy_close",
    "strategy_close_positions", "strategy_update", "strategy_pause", "strategy_top_up",
    "strategy_withdraw_funds", "strategy_bridge_funds_from_hyperliquid_to_evm", "ratchet_stop_add",
    "ratchet_stop_edit", "ratchet_stop_delete", "user_claim_referral_rewards",
}

# Credentials the build engine's process can see. Naming one is fine — a strategy.yaml legitimately
# says `auth_token_env: SENPI_AUTH_TOKEN`; reading its VALUE is not.
_SECRETS = r"(SENPI_AUTH_TOKEN|SENPI_API_KEY|AI_API_KEY|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|SENPI_AUTHOR_ANTHROPIC_API_KEY)"

# Checked against the WHOLE command, heredoc bodies included: money, credentials, deploy.
STRICT_RULES = [
    (r"\bdeploy\.py\b[^;&|]*\b(create|runtime|update|fork)\b", "deploying/updating is senpi-strategy-ops' job, after the user's yes — never the build engine's"),
    (r"\bclose\.py\b", "closing strategies is not part of authoring"),
    (r"\bauthor_build\.py\b[^;&|]*\b(start|answer|promote|_run)\b", "the build engine does not start or promote jobs"),
    # `openclaw` as the command word only — `/data/.openclaw/skills/...` paths are everywhere.
    (r"(?:^|[\s;&|(`])(?:\S*/)?openclaw(?=\s|$)(?!\s+senpi\s+(validate|guide)\b)", "only `openclaw senpi validate` and `openclaw senpi guide` are available to the build engine"),
    (r"\b(" + "|".join(sorted(MUTATING)) + r")\b", "that names a money-moving Senpi tool"),
    (r"\$\{?" + _SECRETS + r"\b|(environ|getenv)\W{0,4}[^\n]{0,40}" + _SECRETS
     + r"|senpi\.token|/proc/[^ ]*/environ|openclaw\.json|\.claude-author", "that reads a credential"),
    (r"\b(printenv|export\s+-p|declare\s+-x)\b|(^|[;&|]\s*)(env|set)\s*($|[;&|])", "environment dumps can expose the user's credentials"),
]
# Checked with heredoc bodies stripped: these words are common in file CONTENT (a docstring that
# says "curl", a helper named kill_switch) and only matter as commands.
COMMAND_RULES = [
    (r"\b(curl|wget|nc|ncat|telnet|ssh|scp)\b", "no direct network access — read market data through the mcp__senpi__* tools"),
    (r"\b(pip3?|uv|npm|pnpm|yarn|apt(-get)?|brew)\s+(install|add|i)\b", "installing software is not allowed on the agent host"),
    (r"\bgit\s+(push|remote)\b|\bgh\s", "no publishing from the build engine"),
    (r"\bcron(tab)?\b", "never schedule agent turns — each firing is a paid model call"),
    (r"\bsudo\b|\bchmod\s+[0-7]*[67][0-7]*\s+/|\bkill(all)?\s", "not needed to build a package"),
]
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.S)


def strip_heredocs(cmd):
    return _HEREDOC.sub("<<HEREDOC", cmd)


def deny(reason):
    print(f"Blocked by the Senpi build guard: {reason}", file=sys.stderr)
    sys.exit(2)


def allowed_roots():
    roots = []
    for var in ("SENPI_AUTHOR_PKG_DIR", "SENPI_AUTHOR_JOB_DIR"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]).resolve())
    return roots


def inside(path, roots):
    return any(path == r or r in path.parents for r in roots)


def check_path(raw, cwd, roots, what):
    p = Path(os.path.expanduser(raw))
    p = (Path(cwd) / p) if not p.is_absolute() else p
    p = p.resolve()
    if not inside(p, roots):
        deny(f"{what} {p} is outside the package directory ({os.environ.get('SENPI_AUTHOR_PKG_DIR')})")


def check_bash(cmd, cwd, roots):
    for pattern, reason in STRICT_RULES:
        if re.search(pattern, cmd, flags=re.MULTILINE):
            deny(reason)
    cmd = strip_heredocs(cmd)
    for pattern, reason in COMMAND_RULES:
        if re.search(pattern, cmd, flags=re.MULTILINE):
            deny(reason)
    # Writes through the shell: redirections and the destination of rm/mv/cp/tee/sed -i.
    for m in re.finditer(r"(?<![0-9&<])>{1,2}\s*([^\s;&|<>]+)", cmd):
        target = m.group(1)
        if target not in ("/dev/null", "/dev/stderr", "/dev/stdout") and not target.startswith("&"):
            check_path(target, cwd, roots, "writing")
    for seg in re.split(r"[;&|]+", cmd):
        try:
            toks = shlex.split(seg)
        except ValueError:
            continue
        if not toks:
            continue
        verb = os.path.basename(toks[0])
        args = [t for t in toks[1:] if not t.startswith("-")]
        if verb == "rm":
            for t in args:
                check_path(t, cwd, roots, "deleting")
        elif verb in ("mv", "cp", "ln", "install", "rsync") and args:
            check_path(args[-1], cwd, roots, "writing")
            if verb == "mv":
                for t in args[:-1]:
                    check_path(t, cwd, roots, "moving")
        elif verb == "tee":
            for t in args:
                check_path(t, cwd, roots, "writing")
        elif verb == "sed" and any(t == "-i" or t.startswith("-i") for t in toks[1:]):
            for t in args[1:]:
                check_path(t, cwd, roots, "editing")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        deny("unreadable hook input")
    tool = event.get("tool_name") or ""
    inp = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.environ.get("SENPI_AUTHOR_PKG_DIR") or os.getcwd()
    roots = allowed_roots()
    if not roots:
        deny("SENPI_AUTHOR_PKG_DIR is not set — the guard cannot tell where writes belong")

    if tool.startswith("mcp__"):
        parts = tool.split("__", 2)
        server, name = (parts[1], parts[2]) if len(parts) == 3 else ("", "")
        if server != "senpi":
            deny(f"MCP server {server!r} is not available to the build engine")
        if name in MUTATING or not (name in READ_EXACT or name.startswith(READ_PREFIXES)):
            deny(f"{name} is not a read-only Senpi tool — the build engine only reads")
    elif tool in ("Write", "Edit", "MultiEdit"):
        check_path(inp.get("file_path") or "", cwd, roots, "writing")
    elif tool == "NotebookEdit":
        check_path(inp.get("notebook_path") or "", cwd, roots, "writing")
    elif tool == "Bash":
        check_bash(inp.get("command") or "", cwd, roots)
    sys.exit(0)


if __name__ == "__main__":
    main()
