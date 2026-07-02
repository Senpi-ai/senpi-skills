#!/usr/bin/env python3
"""senpi-help engine — the LIVE capability directory.

Reads the INSTALLED skill set (the realized manifest) and emits each skill + what it's for,
straight from every SKILL.md's own `description`. Nothing is hardcoded here, so the directory
can never drift as skills are added / renamed / removed.

For raw MCP TOOLS (not skills), the authoritative catalog is the Senpi overview guide:
  read_senpi_guide(uri=senpi://guides/senpi-overview)

Usage:
  python3 scripts/help.py          # JSON: {skills_root, count, skills:[{name, use_for}]}
  python3 scripts/help.py --md     # the same as a readable directory

Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
"""
import json
import os
import re
import sys
from pathlib import Path


def skills_root() -> Path:
    """The directory that holds the installed skill folders (senpi-help + siblings)."""
    env = os.environ.get("SENPI_SKILLS_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve()  # <root>/senpi-help/scripts/help.py
    if len(here.parents) >= 3 and (here.parents[2] / "senpi-help").is_dir():
        return here.parents[2]
    for p in ("/data/.openclaw/skills",
              str(Path.home() / ".openclaw" / "skills"),
              str(Path.home() / ".agents" / "skills")):
        if Path(p).is_dir():
            return Path(p)
    return here.parents[2] if len(here.parents) >= 3 else here.parent


def _frontmatter(text: str) -> str:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    return m.group(1) if m else ""


def _field(fm: str, key: str) -> str:
    # inline: `key: value`
    m = re.search(rf"^{key}:[ \t]*(?![>|])(\S.*)$", fm, re.M)
    if m:
        return m.group(1).strip().strip("\"'")
    # folded/literal block: `key: >-` (or |) then indented continuation lines
    m = re.search(rf"^{key}:[ \t]*[>|]-?[ \t]*\n((?:[ \t]+.*\n?)+)", fm, re.M)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    return ""


def collect(root: Path) -> list:
    skills = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        try:
            fm = _frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        name = _field(fm, "name") or skill_md.parent.name
        if name == "senpi-help":
            continue  # don't list the directory in itself
        skills.append({"name": name, "use_for": _field(fm, "description")})
    return skills


def main(argv) -> int:
    root = skills_root()
    skills = collect(root)
    tools_note = "For raw MCP tools, the authoritative catalog is read_senpi_guide(uri=senpi://guides/senpi-overview)."
    if "--md" in argv:
        print(f"# Installed Senpi skills ({len(skills)}) — match the user's intent to one\n")
        for s in skills:
            print(f"- **{s['name']}** — {s['use_for']}")
        print(f"\n**Tools →** {tools_note}")
    else:
        print(json.dumps({"skills_root": str(root), "count": len(skills),
                          "skills": skills, "tools_note": tools_note}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
