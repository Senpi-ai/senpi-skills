#!/usr/bin/env node
// Validates compatibility.json. Invoked by .github/workflows/compat-lint.yml
// on every PR that touches the file. Fails the PR with precise error
// messages when entries are malformed.
//
// Checks every entry against the schema in
// `/Users/yosephks/.claude/plans/senpi-skills-side-spec.md` §3:
//   1. Top-level value is an object of objects keyed by /^\d+\.\d+$/.
//   2. runtimes entries match /^\d+\.\d+$/. A given runtime major.minor
//      appears in at most one band across the entire file.
//   3. releases keys match /^\d+\.\d+\.\d+$/ and their major.minor equals
//      the band key.
//   4. releases values match /^[0-9a-f]{40}$/.
//   5. Each SHA is reachable in the local repo (full history is fetched
//      by the workflow).
//   6. <skill>/SKILL.md exists at that SHA, its YAML frontmatter parses,
//      and its metadata.version equals the releases key.

import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";

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

// Map of runtime majorMinor -> "skillName/bandKey" that already claimed it.
// A given runtime version must be served by exactly one band across the file.
const seenRuntimes = new Map();

for (const [skillName, bands] of Object.entries(compat)) {
  if (bands === null || typeof bands !== "object" || Array.isArray(bands)) {
    errors.push(`Skill "${skillName}" value must be an object of bands.`);
    continue;
  }

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
            `Runtime "${rt}" is listed in both ${owner} and ${skillName}/${bandKey} — must appear in at most one band.`
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
        execSync(`git cat-file -e ${sha}^{commit}`, { stdio: "pipe" });
      } catch {
        errors.push(
          `${skillName}/${bandKey}/${version}: SHA ${sha} is not reachable in this repository.`
        );
        continue;
      }

      const skillMdPath = `${skillName}/SKILL.md`;
      let skillMdAtSha;
      try {
        skillMdAtSha = execSync(`git show ${sha}:${skillMdPath}`, {
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
