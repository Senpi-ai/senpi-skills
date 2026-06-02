#!/usr/bin/env node
// Validates compatibility.json. Invoked by .github/workflows/compat-lint.yml
// on every PR that touches the file. Fails the PR with precise error
// messages when entries are malformed.
//
// Checks:
//   1. Top-level value is an object of objects keyed by /^\d+\.\d+$/.
//   2. runtimes entries match /^\d+\.\d+$/. A given runtime major.minor
//      appears in at most one band within a single skill — different skills
//      can independently claim compatibility with the same runtime line.
//   3. releases keys match /^\d+\.\d+\.\d+$/ and their major.minor equals
//      the band key.
//   4. releases values match /^[0-9a-f]{40}$/.
//   5. Each SHA is reachable in the local repo (full history is fetched
//      by the workflow).
//   6. <skill>/SKILL.md exists at that SHA, its YAML frontmatter parses,
//      and its metadata.version equals the releases key.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";

// Defense in depth: skill names become argv to `git show <sha>:<name>/SKILL.md`.
// execFileSync already avoids the shell, but constraining the key shape catches
// obviously-malformed registry entries up front and rules out git-arg injection
// tricks (leading "-", path traversal, etc).
const SKILL_NAME_RE = /^[a-z0-9][a-z0-9_-]*$/;

const compatPath = "compatibility.json";

let compat;
try {
  compat = JSON.parse(readFileSync(compatPath, "utf8"));
} catch (err) {
  console.error(`compat-lint: cannot read/parse ${compatPath}: ${err.message}`);
  process.exit(1);
}

const errors = [];

if (compat === null || typeof compat !== "object" || Array.isArray(compat)) {
  errors.push("Top-level value must be an object (skill -> bands).");
  reportAndExit();
}

for (const [skillName, bands] of Object.entries(compat)) {
  if (!SKILL_NAME_RE.test(skillName)) {
    errors.push(
      `Skill key "${skillName}" must match ${SKILL_NAME_RE} (lowercase alphanumeric, hyphens, underscores).`
    );
    continue;
  }

  if (bands === null || typeof bands !== "object" || Array.isArray(bands)) {
    errors.push(`Skill "${skillName}" value must be an object of bands.`);
    continue;
  }

  // Per-skill: a runtime major.minor must be served by exactly one band of
  // THIS skill. Different skills may independently claim the same runtime.
  const seenRuntimes = new Map();

  for (const [bandKey, band] of Object.entries(bands)) {
    if (!/^\d+\.\d+$/.test(bandKey)) {
      errors.push(`Bad band key "${bandKey}" under skill "${skillName}" (must match \\d+\\.\\d+).`);
      continue;
    }

    if (band === null || typeof band !== "object" || Array.isArray(band)) {
      errors.push(`${skillName}/${bandKey}: band value must be an object.`);
      continue;
    }

    // runtimes
    const runtimes = band.runtimes;
    if (!Array.isArray(runtimes) || runtimes.length === 0) {
      errors.push(`${skillName}/${bandKey}: runtimes must be a non-empty array.`);
    } else {
      for (const rt of runtimes) {
        if (typeof rt !== "string" || !/^\d+\.\d+$/.test(rt)) {
          errors.push(`${skillName}/${bandKey}: bad runtime "${rt}" (must match \\d+\\.\\d+).`);
          continue;
        }
        const owner = seenRuntimes.get(rt);
        if (owner) {
          errors.push(
            `Runtime "${rt}" is listed in both ${owner} and ${skillName}/${bandKey} — within a single skill it must appear in at most one band.`
          );
        } else {
          seenRuntimes.set(rt, `${skillName}/${bandKey}`);
        }
      }
    }

    // releases
    const releases = band.releases;
    if (releases === null || typeof releases !== "object" || Array.isArray(releases)) {
      errors.push(`${skillName}/${bandKey}: releases must be an object.`);
      continue;
    }

    for (const [version, sha] of Object.entries(releases)) {
      if (!/^\d+\.\d+\.\d+$/.test(version)) {
        errors.push(
          `${skillName}/${bandKey}: release key "${version}" must be three-part SemVer.`
        );
        continue;
      }
      const [major, minor] = version.split(".");
      if (`${major}.${minor}` !== bandKey) {
        errors.push(
          `${skillName}/${bandKey}: release "${version}" major.minor does not match band "${bandKey}".`
        );
      }
      if (typeof sha !== "string" || !/^[0-9a-f]{40}$/.test(sha)) {
        errors.push(
          `${skillName}/${bandKey}/${version}: SHA "${sha}" must be a 40-char lowercase hex string.`
        );
        continue;
      }

      try {
        // execFileSync (no shell) — sha is hex-validated above, but we use
        // execFile across the board so registry keys can never reach a shell.
        execFileSync("git", ["cat-file", "-e", `${sha}^{commit}`], { stdio: "pipe" });
      } catch {
        errors.push(
          `${skillName}/${bandKey}/${version}: SHA ${sha} is not reachable in this repository.`
        );
        continue;
      }

      const skillMdPath = `${skillName}/SKILL.md`;
      let skillMdAtSha;
      try {
        skillMdAtSha = execFileSync("git", ["show", `${sha}:${skillMdPath}`], {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
        });
      } catch {
        errors.push(
          `${skillName}/${bandKey}/${version}: no ${skillMdPath} at SHA ${sha}.`
        );
        continue;
      }

      const fmMatch = skillMdAtSha.match(/^---\n([\s\S]*?)\n---/);
      if (!fmMatch) {
        errors.push(
          `${skillName}/${bandKey}/${version}: ${skillMdPath} at SHA ${sha} has no YAML frontmatter.`
        );
        continue;
      }

      let fm;
      try {
        fm = parseYaml(fmMatch[1]);
      } catch (err) {
        errors.push(
          `${skillName}/${bandKey}/${version}: YAML frontmatter parse error at SHA ${sha}: ${err.message}.`
        );
        continue;
      }

      const fileVersion = fm?.metadata?.version;
      if (fileVersion == null) {
        errors.push(
          `${skillName}/${bandKey}/${version}: ${skillMdPath} at SHA ${sha} has no metadata.version field.`
        );
        continue;
      }
      if (String(fileVersion) !== version) {
        errors.push(
          `${skillName}/${bandKey}/${version}: ${skillMdPath} at SHA ${sha} says metadata.version "${fileVersion}", registry says "${version}".`
        );
      }
    }
  }
}

reportAndExit();

function reportAndExit() {
  if (errors.length > 0) {
    console.error("compatibility.json validation failed:\n");
    for (const e of errors) console.error("  - " + e);
    process.exit(1);
  }
  console.log("compatibility.json OK");
}
