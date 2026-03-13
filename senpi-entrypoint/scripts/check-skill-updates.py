#!/usr/bin/env python3
"""
check-skill-updates.py — Senpi Skill Update Checker

Reads the Vercel skills CLI global lock file (~/.agents/.skill-lock.json) to find
all installed Senpi skills, then checks GitHub for:
  - Version bumps (hash change in skill folder + version field changed in SKILL.md)
  - New skills added to the repo that the user has never been shown

Uses `$SENPI_STATE_DIR/pending-skill-updates.json` (default:
`~/.config/senpi/pending-skill-updates.json` if `SENPI_STATE_DIR` is unset)
to track last-known versions and which skills have already been surfaced to the user.

Output contract:
  { "success": true, "updatedSkills": [...], "newSkills": [...] }
  OR { "heartbeat": "HEARTBEAT_OK" }
  OR { "heartbeat": "HEARTBEAT_OK" }  (on error in interactive mode)
  In --cron mode, this script stays fully silent on stdout.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

SENPI_REPO = "Senpi-ai/senpi-skills"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

LOCK_FILE = os.path.expanduser("~/.agents/.skill-lock.json")
SENPI_STATE_DIR = os.path.expanduser(os.environ.get("SENPI_STATE_DIR") or "~/.config/senpi")
CATALOG_FILE = os.path.join(SENPI_STATE_DIR, "skills-catalog.json")
STATE_FILE = os.path.join(SENPI_STATE_DIR, "state.json")
PENDING_FILE = os.path.join(SENPI_STATE_DIR, "pending-skill-updates.json")

# Top-level repo directories that are never skills
NON_SKILL_DIRS = {
    ".github", ".git", "node_modules", "__pycache__", "dist", "docs",
    ".claude", ".agents", ".cursor",
}

# Skills whose SKILL.md is not at <skill_name>/SKILL.md in the repo root.
SKILL_MD_PATHS = {
    "fox-strategy": "fox/skills/fox-strategy/SKILL.md",
}

# Skills whose GitHub top-level directory name differs from their install name.
SKILL_GITHUB_DIRS = {
    "fox-strategy": "fox",
}

# Reverse map: top-level dirs that are containers, not skills themselves.
# Maps github dir name -> install skill name for proper discovery handling.
GITHUB_DIR_TO_SKILL = {v: k for k, v in SKILL_GITHUB_DIRS.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def merge_pending_results(existing, incoming):
    """
    Merge cron output with already-pending notifications.
    Prevents later cron runs from overwriting unsurfaced items.
    """
    if not isinstance(existing, dict):
        existing = {}
    if not isinstance(incoming, dict):
        incoming = {}

    merged_updated = []
    updated_by_name = {}

    for item in existing.get("updatedSkills") or []:
        name = item.get("name")
        if not name:
            continue
        normalized = {
            "name": name,
            "oldVersion": item.get("oldVersion") or "unknown",
            "newVersion": item.get("newVersion") or item.get("oldVersion") or "unknown",
        }
        updated_by_name[name] = len(merged_updated)
        merged_updated.append(normalized)

    for item in incoming.get("updatedSkills") or []:
        name = item.get("name")
        if not name:
            continue
        incoming_old = item.get("oldVersion") or "unknown"
        incoming_new = item.get("newVersion") or incoming_old

        if name in updated_by_name:
            idx = updated_by_name[name]
            prev = merged_updated[idx]
            old_version = prev.get("oldVersion") or "unknown"
            if old_version == "unknown" and incoming_old != "unknown":
                old_version = incoming_old
            merged_updated[idx] = {
                "name": name,
                "oldVersion": old_version,
                "newVersion": incoming_new,
            }
        else:
            updated_by_name[name] = len(merged_updated)
            merged_updated.append({
                "name": name,
                "oldVersion": incoming_old,
                "newVersion": incoming_new,
            })

    merged_new = []
    new_by_name = {}

    for item in existing.get("newSkills") or []:
        name = item.get("name")
        if not name:
            continue
        normalized = {
            "name": name,
            "version": item.get("version") or "latest",
            "description": (item.get("description") or "").strip(),
        }
        new_by_name[name] = len(merged_new)
        merged_new.append(normalized)

    for item in incoming.get("newSkills") or []:
        name = item.get("name")
        if not name:
            continue
        normalized = {
            "name": name,
            "version": item.get("version") or "latest",
            "description": (item.get("description") or "").strip(),
        }
        if name in new_by_name:
            merged_new[new_by_name[name]] = normalized
        else:
            new_by_name[name] = len(merged_new)
            merged_new.append(normalized)

    return {
        "success": True,
        "updatedSkills": merged_updated,
        "newSkills": merged_new,
    }


def apply_pending_to_catalog(known_versions, seen_available, pending_result):
    """Ensure catalog state reflects everything currently queued in pending."""
    for item in pending_result.get("updatedSkills") or []:
        name = item.get("name")
        new_version = item.get("newVersion")
        if name and new_version and new_version != "unknown":
            known_versions[name] = new_version

    for item in pending_result.get("newSkills") or []:
        name = item.get("name")
        if not name:
            continue
        seen_available.add(name)
        version = item.get("version")
        if version and version != "latest":
            known_versions[name] = version


def github_get(url, max_attempts=3, delay=3):
    """GET a GitHub API URL. Returns parsed JSON or None after all retries fail."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "senpi-skill-update-checker/1.0"},
    )
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            if attempt < max_attempts - 1:
                time.sleep(delay)
    return None


def github_raw(path, max_attempts=2, delay=1):
    """Fetch raw file content from GitHub main branch. Returns text or None after all retries fail."""
    url = f"{GITHUB_RAW}/{SENPI_REPO}/main/{path}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "senpi-skill-update-checker/1.0"},
    )
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode()
        except Exception:
            if attempt < max_attempts - 1:
                time.sleep(delay)
    return None


def parse_frontmatter_field(skill_md_text, field):
    """
    Extract a scalar or block-scalar field from YAML frontmatter.
    Supports dotted paths for nested fields (e.g. "metadata.version").
    Handles both:
      field: "simple value"
      field: >
        multi-line
        value
    Returns the value as a string, or None if not found.
    """
    if not skill_md_text:
        return None

    if "." in field:
        parent, child = field.split(".", 1)
        return _parse_nested_frontmatter_field(skill_md_text, parent, child)

    lines = skill_md_text.split("\n")
    in_frontmatter = False
    collecting = False
    block_style = None
    collected = []

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter and line.rstrip() == "---":
            break
        if not in_frontmatter:
            continue

        if collecting:
            if line.strip() == "":
                # Blank lines are valid inside YAML block scalars.
                collected.append("")
            elif line.startswith("  ") or line.startswith("\t"):
                collected.append(line.strip())
            else:
                # End of block scalar
                break
        elif line.startswith(f"{field}:"):
            val = line.split(":", 1)[1].strip()
            if val in (">", ">-", "|", "|-"):
                collecting = True
                block_style = val
            else:
                return val.strip('"').strip("'")

    if collected:
        if block_style and block_style.startswith("|"):
            parsed = "\n".join(collected).strip()
            return parsed if parsed else None

        # Folded scalar: fold lines in each paragraph, preserve blank lines
        # as paragraph breaks.
        paragraphs = []
        current_paragraph = []
        for block_line in collected:
            if block_line == "":
                if current_paragraph:
                    paragraphs.append(" ".join(current_paragraph).strip())
                    current_paragraph = []
                elif paragraphs:
                    # Preserve repeated blank lines between paragraphs.
                    paragraphs.append("")
            else:
                current_paragraph.append(block_line)

        if current_paragraph:
            paragraphs.append(" ".join(current_paragraph).strip())

        parsed = "\n\n".join(paragraphs).strip()
        return parsed if parsed else None
    return None


def _parse_nested_frontmatter_field(skill_md_text, parent, child):
    """Find a scalar field nested (indented) under a parent key in YAML frontmatter."""
    lines = skill_md_text.split("\n")
    in_frontmatter = False
    in_parent = False

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter and line.rstrip() == "---":
            break
        if not in_frontmatter:
            continue

        if in_parent:
            if line.startswith("  ") or line.startswith("\t"):
                stripped = line.strip()
                if stripped.startswith(f"{child}:"):
                    val = stripped.split(":", 1)[1].strip()
                    return val.strip('"').strip("'")
            elif line and not line[0].isspace():
                # Returned to top-level — parent block ended without finding child
                in_parent = False
        elif line.startswith(f"{parent}:"):
            in_parent = True

    return None


def is_skill_dir(entry):
    """True if this GitHub contents entry looks like a skill directory."""
    return (
        entry.get("type") == "dir"
        and not entry["name"].startswith(".")
        and entry["name"] not in NON_SKILL_DIRS
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_runtime_args(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--cron", action="store_true",
        help="Background mode: write results to pending file instead of stdout",
    )
    args, _ = parser.parse_known_args(argv)
    return args


def main(args):
    # 0. Runtime flags are parsed once in __main__ and passed in.

    # 1. Check opt-out flag in Senpi state
    state = load_json(STATE_FILE)
    if state.get("skillUpdates", {}).get("enabled") is False:
        if not args.cron:
            print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
        return

    # 2. Read Vercel skills CLI global lock file
    lock = load_json(LOCK_FILE)
    if not lock:
        if not args.cron:
            print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
        return

    all_installed = lock.get("skills", {})

    # Filter to Senpi skills only (source or sourceUrl contains the repo)
    senpi_skills = {
        name: entry
        for name, entry in all_installed.items()
        if SENPI_REPO in (entry.get("source") or "")
        or SENPI_REPO.lower() in (entry.get("sourceUrl") or "").lower()
    }

    # 3. Load local version catalog (tracks last-known versions + seen-available set)
    catalog = load_json(CATALOG_FILE, {
        "version": 1,
        "knownVersions": {},
        "seenAvailable": [],
        "lastChecked": None,
    })
    known_versions = catalog.get("knownVersions") or {}
    seen_available = set(catalog.get("seenAvailable") or [])

    # 4. Fetch GitHub repo root directory listing (1 API call)
    repo_contents = github_get(f"{GITHUB_API}/repos/{SENPI_REPO}/contents/")
    if not repo_contents:
        # Can't reach GitHub — exit silently, don't fail the agent
        if not args.cron:
            print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
        return

    # Build a map of { dir_name -> {"sha": "<tree-sha>", ...} }
    github_skill_dirs = {
        entry["name"]: entry
        for entry in repo_contents
        if is_skill_dir(entry)
    }

    updated_skills = []
    new_skills = []

    # 5. For each installed Senpi skill, check if the folder hash changed
    for skill_name, lock_entry in senpi_skills.items():
        stored_hash = lock_entry.get("skillFolderHash") or ""

        # The GitHub contents API returns each folder entry with its git tree SHA.
        # This is the same SHA the skills CLI stores as skillFolderHash.
        github_dir_name = SKILL_GITHUB_DIRS.get(skill_name, skill_name)
        current_github_entry = github_skill_dirs.get(github_dir_name)
        if not current_github_entry:
            # Skill directory no longer exists on GitHub (unlikely but handle gracefully)
            continue

        github_sha = current_github_entry.get("sha", "")

        skill_md_path = SKILL_MD_PATHS.get(skill_name, f"{skill_name}/SKILL.md")

        if stored_hash and github_sha == stored_hash:
            # Hash unchanged. Seed known_versions on first encounter so that if
            # the hash ever changes in the future, old_version is the correct
            # baseline and not None (which would produce a spurious "unknown → vX"
            # report even for docs-only changes where the version didn't bump).
            if skill_name not in known_versions:
                skill_md_text = github_raw(skill_md_path)
                version = parse_frontmatter_field(skill_md_text, "metadata.version")
                if version:
                    known_versions[skill_name] = version
            continue

        # Hash changed — fetch SKILL.md to check if the version field bumped
        skill_md_text = github_raw(skill_md_path)
        new_version = parse_frontmatter_field(skill_md_text, "metadata.version")

        if not new_version:
            # No version in frontmatter — not a versioned skill, skip
            continue

        old_version = known_versions.get(skill_name)

        if old_version and old_version == new_version:
            # Hash changed (docs/scripts) but version is the same — not a version bump, skip
            continue

        updated_skills.append({
            "name": skill_name,
            "oldVersion": old_version or "unknown",
            "newVersion": new_version,
        })
        known_versions[skill_name] = new_version

    # 6. Detect skills in the GitHub repo that this user has never seen
    installed_names = set(senpi_skills.keys())

    # On the very first run the catalog doesn't exist yet, so seenAvailable is
    # empty. Without special-casing this, every non-installed skill in the repo
    # would be surfaced as "new" — producing noisy output when there is nothing
    # meaningful to compare against. Instead, silently baseline all current
    # GitHub skill dirs so we only surface skills added *after* this first run.
    is_first_run = catalog.get("lastChecked") is None

    for dir_name, dir_entry in github_skill_dirs.items():
        # If this dir is a container for a differently-named skill, use the
        # install name so lock-file lookups and seen_available tracking are correct.
        skill_name = GITHUB_DIR_TO_SKILL.get(dir_name, dir_name)
        skill_md_path = SKILL_MD_PATHS.get(skill_name, f"{dir_name}/SKILL.md")

        if skill_name in installed_names:
            # Installed skills are already known to the user. Mark them as seen
            # so uninstalling later doesn't surface them as "newly added".
            seen_available.add(skill_name)
            continue
        if skill_name in seen_available:
            continue

        if is_first_run:
            # Silently mark as seen — don't surface pre-existing skills on first run.
            seen_available.add(skill_name)
            continue

        # Genuinely new skill added to the repo since our last check — fetch metadata.
        skill_md_text = github_raw(skill_md_path)
        if not skill_md_text:
            continue

        version = parse_frontmatter_field(skill_md_text, "metadata.version")
        description = parse_frontmatter_field(skill_md_text, "description")

        new_skills.append({
            "name": skill_name,
            "version": version or "latest",
            "description": (description or "").strip(),
        })

        # Mark as seen so we don't surface it again next check
        seen_available.add(skill_name)
        # Also seed known version so future checks detect bumps for this skill too
        if version:
            known_versions[skill_name] = version

    result = None
    if updated_skills or new_skills:
        result = {"success": True, "updatedSkills": updated_skills, "newSkills": new_skills}

    # 7. In cron mode, merge into pending before advancing catalog state.
    # This prevents later runs from dropping unsurfaced notifications.
    if args.cron and result:
        existing_pending = load_json(PENDING_FILE, {})
        merged_pending = merge_pending_results(existing_pending, result)
        apply_pending_to_catalog(known_versions, seen_available, merged_pending)
        atomic_write(PENDING_FILE, merged_pending)

    # 8. Atomically update the catalog after pending is durable.
    catalog["knownVersions"] = known_versions
    catalog["seenAvailable"] = sorted(seen_available)
    catalog["lastChecked"] = datetime.now(timezone.utc).isoformat()
    atomic_write(CATALOG_FILE, catalog)

    # 9. Output
    if not result:
        if not args.cron:
            print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
    elif not args.cron:
        print(json.dumps(result))


if __name__ == "__main__":
    runtime_args = parse_runtime_args(sys.argv[1:])
    try:
        main(runtime_args)
    except Exception:
        # Never crash the agent. In cron mode, remain fully silent.
        if not runtime_args.cron:
            print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
