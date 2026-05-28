#!/usr/bin/env node
// Appends entries to compatibility.json for SKILL.md version bumps in the
// just-pushed commit. Invoked by .github/workflows/compat-update.yml.
//
// Argv:
//   [2] full 40-char hex SHA of the commit to record (typically ${{ github.sha }})
// Env:
//   CHANGED_FILES: newline-separated list of <skill>/SKILL.md paths that
//                  changed in this push (flat layout — no skills/ prefix).
//
// Behaviour:
//   - Skips silently if the SKILL.md version isn't three-part SemVer.
//   - Skips silently if the skill isn't tracked in compatibility.json yet
//     (only senpi-trading-runtime is tracked today; others are no-ops).
//   - Errors hard if the skill IS tracked but the matching band doesn't
//     exist — that means a human policy decision is needed (new runtimes
//     array) and the bump should not be silently dropped.

import { readFileSync, writeFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";

const sha = process.argv[2];
if (!/^[0-9a-f]{40}$/.test(sha || "")) {
  console.error(`append-releases: bad SHA argument: "${sha}" (must be 40-char hex)`);
  process.exit(1);
}

const changedFiles = (process.env.CHANGED_FILES || "")
  .split("\n")
  .map((s) => s.trim())
  .filter(Boolean);

if (changedFiles.length === 0) {
  console.log("append-releases: no SKILL.md files in CHANGED_FILES — nothing to do");
  process.exit(0);
}

const compatPath = "compatibility.json";
const compat = JSON.parse(readFileSync(compatPath, "utf8"));

let appended = 0;
let hadFatal = false;

for (const file of changedFiles) {
  // Flat layout: "<skill>/SKILL.md"
  const m = file.match(/^([^/]+)\/SKILL\.md$/);
  if (!m) {
    console.log(`append-releases: ignoring non-skill path "${file}"`);
    continue;
  }
  const skillName = m[1];

  let skillMd;
  try {
    skillMd = readFileSync(file, "utf8");
  } catch (err) {
    console.log(`append-releases: cannot read ${file} (${err.code}) — skipping`);
    continue;
  }

  const fmMatch = skillMd.match(/^---\n([\s\S]*?)\n---/);
  if (!fmMatch) {
    console.log(`append-releases: no YAML frontmatter in ${file} — skipping`);
    continue;
  }

  let fm;
  try {
    fm = parseYaml(fmMatch[1]);
  } catch (err) {
    console.log(`append-releases: YAML parse failed for ${file} (${err.message}) — skipping`);
    continue;
  }

  const rawVersion = fm?.metadata?.version;
  if (rawVersion == null) {
    console.log(`append-releases: ${file} has no metadata.version — skipping`);
    continue;
  }
  const version = String(rawVersion);

  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    console.log(
      `append-releases: ${file} version "${version}" is not three-part SemVer — skipping (not eligible for the registry)`
    );
    continue;
  }

  const skillEntries = compat[skillName];
  if (!skillEntries) {
    console.log(
      `append-releases: skill "${skillName}" is not tracked in compatibility.json — skipping (add it manually to opt in)`
    );
    continue;
  }

  const [major, minor] = version.split(".");
  const expectedBand = `${major}.${minor}`;
  const band = skillEntries[expectedBand];
  if (!band) {
    console.error(
      `append-releases: skill "${skillName}" is tracked but band "${expectedBand}" does not exist. ` +
        `Add the band entry (with a runtimes array) to compatibility.json manually before bumping SKILL.md to ${version}.`
    );
    hadFatal = true;
    continue;
  }

  if (!band.releases || typeof band.releases !== "object" || Array.isArray(band.releases)) {
    // compat-lint rejects arrays/non-objects on PR-time, so this branch is a
    // defensive recovery rather than a normal code path. Reset to an empty
    // object so the subsequent assignment isn't lost (JSON.stringify silently
    // drops non-integer keys on arrays).
    band.releases = {};
  }

  if (band.releases[version]) {
    if (band.releases[version] === sha) {
      console.log(
        `append-releases: ${skillName}/${expectedBand}/${version} already pinned to ${sha} — no-op`
      );
    } else {
      // Skip, don't fail. This branch fires when:
      //  (a) compat.json was hand-seeded with a pre-merge SHA (e.g. the
      //      initial registry commit), then the merge fires this workflow
      //      and github.sha is the merge commit — different from the seed.
      //  (b) Someone edits SKILL.md without bumping the version (e.g. a
      //      typo fix). Path-filter still triggers; version is unchanged.
      // In both cases the registry already has a valid entry. compat-lint
      // is the source of truth for "is the registered SHA self-consistent";
      // we leave it alone here. Manual drift surfaces on the next
      // compat.json PR via the lint.
      console.log(
        `append-releases: ${skillName}/${expectedBand}/${version} already exists with a different SHA ` +
          `(registered ${band.releases[version]}, this push ${sha}) — leaving registry entry as-is`
      );
    }
    continue;
  }

  band.releases[version] = sha;
  console.log(`append-releases: appended ${skillName}/${expectedBand}/${version} = ${sha}`);
  appended++;
}

if (hadFatal) {
  process.exit(1);
}

if (appended === 0) {
  console.log("append-releases: nothing appended — leaving compatibility.json untouched");
  process.exit(0);
}

writeFileSync(compatPath, JSON.stringify(compat, null, 2) + "\n");
console.log(`append-releases: wrote ${appended} new entr${appended === 1 ? "y" : "ies"} to compatibility.json`);
