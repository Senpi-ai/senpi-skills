#!/usr/bin/env python3
"""
check-skill-updates.py — Senpi Skill Update Checker

Reads the Vercel skills CLI global lock file (~/.agents/.skill-lock.json) to find
all installed Senpi skills, then checks GitHub for:
  - Version bumps (hash change in skill folder + version field changed in SKILL.md)
  - New skills added to the repo that the user has never been shown

Uses ~/.config/senpi/skills-catalog.json to track last-known versions and
which skills have already been surfaced to the user.

Output contract:
  { "success": true, "updatedSkills": [...], "newSkills": [...] }
  OR { "heartbeat": "HEARTBEAT_OK" }
  OR { "heartbeat": "HEARTBEAT_OK" }  (on any error — never crash the agent)
"""

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
CATALOG_FILE = os.path.expanduser("~/.config/senpi/skills-catalog.json")
STATE_FILE = os.path.expanduser("~/.config/senpi/state.json")

# Top-level repo directories that are never skills
NON_SKILL_DIRS = {
    ".github", ".git", "node_modules", "__pycache__", "dist", "docs",
    ".claude", ".agents", ".cursor",
}


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


def github_raw(path, max_attempts=3, delay=3):
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
    Handles both:
      field: "simple value"
      field: >
        multi-line
        value
    Returns the value as a string, or None if not found.
    """
    if not skill_md_text:
        return None

    lines = skill_md_text.split("\n")
    in_frontmatter = False
    collecting = False
    collected = []

    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter and line.strip() == "---":
            break
        if not in_frontmatter:
            continue

        if collecting:
            if line.startswith("  ") or line.startswith("\t"):
                collected.append(line.strip())
            else:
                # End of block scalar
                break
        elif line.startswith(f"{field}:"):
            val = line.split(":", 1)[1].strip()
            if val in (">", ">-", "|", "|-"):
                collecting = True
            else:
                return val.strip('"').strip("'")

    if collected:
        return " ".join(collected)
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

def main():
    # 1. Check opt-out flag in Senpi state
    state = load_json(STATE_FILE)
    if state.get("skillUpdates", {}).get("enabled") is False:
        print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
        return

    # 2. Read Vercel skills CLI global lock file
    lock = load_json(LOCK_FILE)
    if not lock:
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
        skill_path = lock_entry.get("skillPath") or skill_name
        stored_hash = lock_entry.get("skillFolderHash") or ""

        # The GitHub contents API returns each folder entry with its git tree SHA.
        # This is the same SHA the skills CLI stores as skillFolderHash.
        current_github_entry = github_skill_dirs.get(skill_name)
        if not current_github_entry:
            # Skill directory no longer exists on GitHub (unlikely but handle gracefully)
            continue

        github_sha = current_github_entry.get("sha", "")

        if stored_hash and github_sha == stored_hash:
            # No change — nothing to surface
            continue

        # Hash changed — fetch SKILL.md to check if the version field bumped
        skill_md_text = github_raw(f"{skill_path}/SKILL.md")
        new_version = parse_frontmatter_field(skill_md_text, "version")

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

    for dir_name, dir_entry in github_skill_dirs.items():
        if dir_name in installed_names:
            continue
        if dir_name in seen_available:
            continue

        # Genuinely new — fetch its SKILL.md metadata
        skill_md_text = github_raw(f"{dir_name}/SKILL.md")
        if not skill_md_text:
            continue

        version = parse_frontmatter_field(skill_md_text, "version")
        description = parse_frontmatter_field(skill_md_text, "description")

        new_skills.append({
            "name": dir_name,
            "version": version or "latest",
            "description": (description or "").strip(),
        })

        # Mark as seen so we don't surface it again next check
        seen_available.add(dir_name)
        # Also seed known version so future checks detect bumps for this skill too
        if version:
            known_versions[dir_name] = version

    # 7. Atomically update the catalog
    catalog["knownVersions"] = known_versions
    catalog["seenAvailable"] = sorted(seen_available)
    catalog["lastChecked"] = datetime.now(timezone.utc).isoformat()
    atomic_write(CATALOG_FILE, catalog)

    # 8. Output
    if not updated_skills and not new_skills:
        print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
    else:
        print(json.dumps({"success": True, "updatedSkills": updated_skills, "newSkills": new_skills}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never crash the agent — fail silently
        print(json.dumps({"heartbeat": "HEARTBEAT_OK"}))
