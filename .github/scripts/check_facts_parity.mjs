// Diffs senpi-trading-runtime/scripts/strategy_facts.py (the Python port the catalog is built
// with) against @senpi-ai/runtime's own strategyFacts(), on every instance of every package.
// The catalog's facts are what the strategy pages show as rules, so a port that drifts from the
// runtime shows rules the engine no longer enforces.
//
// Usage (from a directory where `@senpi-ai/runtime` and `yaml` are installed; the script must
// sit in that directory too, since ES modules resolve imports next to the script):
//   node check_facts_parity.mjs <senpi-skills checkout> <python facts JSON>
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { parse } from "yaml";
import { strategyFacts } from "@senpi-ai/runtime/dist/strategy/facts.js";

const [root, pyPath] = process.argv.slice(2);
const py = JSON.parse(readFileSync(pyPath, "utf8"));
const canon = (v) => JSON.stringify(v, (_, x) =>
  x && typeof x === "object" && !Array.isArray(x)
    ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b)))
    : x);

const diffs = [];
let n = 0;
for (const id of readdirSync(join(root, "strategies")).sort()) {
  const manPath = join(root, "strategies", id, "strategy.yaml");
  if (!existsSync(manPath)) continue;
  const man = parse(readFileSync(manPath, "utf8")) ?? {};
  for (const inst of man.instances ?? []) {
    const key = `${id}/${inst.name}`;
    const rt = parse(readFileSync(join(root, "strategies", id, inst.runtime), "utf8")) ?? {};
    const ts = strategyFacts(rt, man);
    n++;
    if (canon(ts) !== canon(py[key])) diffs.push(`${key}\n  runtime: ${canon(ts)}\n  python:  ${canon(py[key])}`);
  }
}
console.log(`${n} instances, ${diffs.length} differ`);
if (diffs.length) {
  console.log(diffs.slice(0, 20).join("\n"));
  process.exit(1);
}
