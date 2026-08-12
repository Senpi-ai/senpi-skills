# Skills Context Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `senpi-strategy-ops/SKILL.md` from 626 to ~260 lines and `senpi-strategy-author/SKILL.md` from 417 to ~300 by deleting prose that mirrors refusal text the runtime already renders — without losing any teaching that steers a real decision.

**Architecture:** Every paragraph is classified into one of four buckets (producer-owned → delete, conversation-owned → keep, routing-owned → keep compressed, reference-owned → move). Producer-owned deletions require a cited `file:line` in the runtime that renders the claim. Where the rendered message is weaker than the prose, the prose stays and a runtime ticket is filed instead. Committed pytest guards keep the deleted mirror from growing back; the dev-box ladder with deliberately injected refusals is the behavioural acceptance test.

**Tech Stack:** Markdown skills, Python 3.11 stdlib + PyYAML, `unittest`/pytest (CI: `.github/workflows/tests.yml`), `openclaw senpi` CLI, Railway dev box.

**Design spec:** `docs/specs/2026-08-12-skills-context-reduction-design.md`

## Global Constraints

- **Branch:** work happens on `feat/skills-context-reduction`, branched from the `feat/deploy-verb-convergence` head and fast-forwarded back into it once Task 8's ladder passes. Never branch off main, and never commit directly to `feat/deploy-verb-convergence` — it is pushed and shared, and a direct commit there ends the fast-forward.
- **Worktree:** `.claude/worktrees/skills-context-reduction`, off the `senpi-skills` checkout. The `senpi-trading-runtime` reference checkout at `origin/feat/senpi-deploy` stays where it is — R1 citations are read from it, never edited.
- **Repo conventions are load-bearing** — read `CLAUDE.md` first. Strategies are packages, not skills. `catalog.json` is GENERATED into two places by `gen_catalog.py`; never hand-edit. Production package is `@senpi-ai/runtime`, never `@senpi/runtime`.
- **No AI attribution in commits or PRs.** No `Co-Authored-By`, no "Generated with" footer.
- **Every content commit bumps the skill's frontmatter `version`** — boxes gate updates on it and will silently never update otherwise.
- **Python 3 stdlib only** in `scripts/` (PyYAML optional, with the vendored loader fallback). No new runtime dependencies.
- **Never delete producer-owned prose without an R1 citation** — the `file:line` in `senpi-trading-runtime` that renders the claim, quoted in the commit message.
- **A weaker producer is a runtime ticket, not a reason to keep prose** (R2). File against #305; leave the prose until the message lands.
- **Runtime reference checkout:** `senpi-trading-runtime` at `origin/feat/senpi-deploy`, side by side. Citations are against that ref.

---

## File Structure

**Created:**
- `senpi-strategy-ops/references/refusal-playbook.md` — per-code depth, read only when a code fires. Bucket-4 destination for `:238-322` and `:324-379`.
- `senpi-strategy-ops/tests/test_skill_surface.py` — the committed guards (budget, code-mention cap, relay-contract purity, link integrity).
- `docs/specs/2026-08-12-classification-table.md` — paragraph → bucket → evidence → disposition. The reviewable artifact.
- `docs/specs/2026-08-12-ladder-results.md` — injection matrix outcomes.

**Modified:**
- `senpi-strategy-ops/SKILL.md` — 626 → ~260
- `senpi-strategy-ops/references/lifecycle.md` — absorbs `verify` internals
- `senpi-strategy-ops/scripts/deploy.py:1514-1535` — proof-aware `validate`
- `senpi-strategy-ops/tests/test_deploy_wrapper.py` — proof-status coverage
- `senpi-strategy-author/SKILL.md` — 417 → ~300
- `docs/error-code-taxonomy.md` — `INVALID_REQUEST` / `NOT_FOUND` rows

---

## Task 1: Proof-aware `validate` (finding 1)

`deploy.py validate` prints `✓ <id>: deploy-ready` without ever reading `.senpi-proof.json`, so the gate most likely to refuse the *next* command is invisible on the surface whose job is "every issue in ONE pass, before you fund anything". No catalog package ships a proof (0 under `strategies/`), so this fires on every fresh catalog deploy.

**Files:**
- Modify: `senpi-strategy-ops/scripts/deploy.py:1514-1535`
- Modify: `senpi-strategy-ops/SKILL.md:494-511` (worked example)
- Test: `senpi-strategy-ops/tests/test_deploy_wrapper.py`

**Interfaces:**
- Consumes: `_pkg.load()` → package with `.instances[]`, each carrying `.runtimeYamlDir`-equivalent (`inst.runtime_yaml_dir` in the Python model — confirm the exact attribute name by reading `_pkg.py` before writing code).
- Produces: `proof_state(pkg) -> list[tuple[str, str, Path]]` — `(instance_name, state, directory)` where `state` is one of `"proven"`, `"no_proof"`. Task 4's guard tests do not depend on this.

- [ ] **Step 1: Read the package model to get the real attribute name**

Run: `grep -n "runtime_yaml_dir\|class Instance\|self\.dir" senpi-strategy-ops/scripts/_pkg.py | head -20`

Use whatever the attribute is actually called. Do not guess.

- [ ] **Step 2: Write the failing test**

Add to `senpi-strategy-ops/tests/test_deploy_wrapper.py`:

```python
class ValidateReportsProofState(unittest.TestCase):
    """`validate` printed `deploy-ready` over a package the very next command refuses.

    No catalog package ships a `.senpi-proof.json`, and `senpi deploy` refuses pre-money without one
    (`[E_VALIDATE_NO_PROOF]`, runtime src/deploy/proof-gate.ts). A preflight that promises "every
    issue in ONE pass, before you fund anything" and cannot see the gate that actually stops the
    deploy is claiming more than it read."""

    def test_unproven_instance_is_named_with_its_validate_command(self):
        pkg = _fixture_package(instances=["swing", "scalp"], proven=["swing"])
        out = _run_validate(pkg)
        self.assertIn("scalp", out)
        self.assertIn("NO PROOF", out)
        self.assertIn("openclaw senpi validate", out)
        self.assertIn("swing", out)

    def test_fully_proven_package_says_so(self):
        pkg = _fixture_package(instances=["main"], proven=["main"])
        out = _run_validate(pkg)
        self.assertIn("proven", out)
        self.assertNotIn("NO PROOF", out)

    def test_json_carries_proof_state_per_instance(self):
        pkg = _fixture_package(instances=["swing", "scalp"], proven=["swing"])
        doc = json.loads(_run_validate(pkg, json_mode=True))
        self.assertEqual(doc["proof"], {"swing": "proven", "scalp": "no_proof"})
```

Write `_fixture_package` and `_run_validate` as local helpers following the tempdir + stub pattern already in `test_run_sh.py` (copy a minimal `strategy.yaml` + per-instance `runtime.yaml`, write `.senpi-proof.json` only for the `proven` list, invoke `deploy.py validate <dir>` via `subprocess`).

- [ ] **Step 3: Run it and verify it fails**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_deploy_wrapper.py -k ValidateReportsProofState -q`
Expected: FAIL — `NO PROOF` absent from output, `KeyError: 'proof'` on the JSON case.

- [ ] **Step 4: Implement `proof_state` and wire it into the validate verdict**

In `deploy.py`, beside `universe_report`:

```python
PROOF_FILE = ".senpi-proof.json"


def proof_state(pkg):
    """Which instances have a recorded `.senpi-proof.json`, and which do not.

    Presence only — this is a local stat, never a re-implementation of the gate. `verifyProof`
    (runtime src/validate/index.ts) owns freshness: content hashes and the runtime build the proof
    was recorded under. Re-deriving either here would be a second copy of a producer, and the
    deploy verb refuses on its own reading regardless of what this reports.

    Returned as `(name, state, dir)` per instance so the caller renders one line each, naming the
    exact directory `openclaw senpi validate` has to be pointed at."""
    out = []
    for inst in pkg.instances:
        d = Path(inst.runtime_yaml_dir)          # confirm the real attribute name in Step 1
        out.append((inst.name, "proven" if (d / PROOF_FILE).is_file() else "no_proof", d))
    return out
```

Then in the `a.cmd == "validate"` block, after `u_errors, u_note = universe_report(pkg)`:

```python
    proofs = proof_state(pkg)
    unproven = [(n, d) for n, s, d in proofs if s == "no_proof"]
    if a.json:
        print(json.dumps({"status": "valid" if not errors else "invalid", "id": pkg.id,
                          "errors": errors, "proof": {n: s for n, s, _ in proofs},
                          **({"note": u_note} if u_note else {})}))
    else:
        if u_note:
            print(f"note: {u_note}", file=sys.stderr)
        if errors:
            print(f"✗ {pkg.id}: {len(errors)} issue(s) to fix before deploy:", file=sys.stderr)
            for e in errors:
                print(f"    - {e}", file=sys.stderr)
        else:
            proven = ", ".join(f"{n} ✓" for n, s, _ in proofs if s == "proven")
            print(f"✓ {pkg.id}: structurally deploy-ready ({len(pkg.instances)} instance(s))"
                  + (f" — proven: {proven}" if proven else ""))
        # The proof gate is the runtime's, and it refuses PRE-MONEY. Naming it here is the whole
        # point: an unproven instance makes the NEXT command fail, not this one.
        for name, d in unproven:
            print(f"  {name}: NO PROOF — `openclaw senpi validate {d}` must PASS before "
                  f"`create` will fund this package", file=sys.stderr)
    sys.exit(EXIT_CODES["refused"] if errors else 0)
```

Note the verdict word changes to `structurally deploy-ready` — the check is structural, and the old bare `deploy-ready` was the overclaim.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_deploy_wrapper.py -k ValidateReportsProofState -q`
Expected: PASS

- [ ] **Step 6: Run the full ops suite for regressions**

Run: `python3 -m pytest senpi-strategy-ops/tests -q`
Expected: all pass (296 passed / 1 skipped before this task, +3 now)

- [ ] **Step 7: Fix the worked example**

In `senpi-strategy-ops/SKILL.md`, replace the numbered worked-example block so the proof step is present and numbered:

```
user: "deploy spider with $300"
1. resolve  → id = spider (two instances: swing 60% / scalp 40%; $300 → swing $180, scalp $120)
              confirm the split with the user BEFORE funding
2. prove    → openclaw senpi validate /data/workspace/strategies/spider/swing   → PASS
              openclaw senpi validate /data/workspace/strategies/spider/scalp   → PASS
              (one run per instance; only a PASS records the proof `create` refuses without)
3. preflight→ python3 scripts/deploy.py validate spider   → structurally deploy-ready, both proven
4. start    → python3 scripts/deploy.py create spider --budget 300
5. watch    → it polls for you; or openclaw senpi deploy status (repeat until terminal)
6. confirm  → the live report + the required How it runs block
```

- [ ] **Step 8: Commit**

```bash
git add senpi-strategy-ops/scripts/deploy.py senpi-strategy-ops/tests/test_deploy_wrapper.py senpi-strategy-ops/SKILL.md
git commit -m "ops: validate said deploy-ready over the gate that stops the deploy

No catalog package ships a .senpi-proof.json and senpi deploy refuses
pre-money without one, so the preflight that promises every issue in one
pass was blind to the only gate that fires on a fresh catalog deploy —
and the worked example, which is the thing agents copy, went straight
from it to create.

Presence only: verifyProof still owns freshness, and this never re-derives
its verdict."
```

---

## Task 2: Precision corrections (findings 2–5)

**Files:**
- Modify: `senpi-strategy-ops/SKILL.md:44`, `:92`, `:160`, `:214`, `:302`, and the `E_VALIDATE_RUNTIME_VERSION_CHANGED` bullet
- Modify: `docs/error-code-taxonomy.md`
- Test: `senpi-strategy-ops/tests/test_skill_surface.py` (created here, one test only)

**Interfaces:**
- Produces: `senpi-strategy-ops/tests/test_skill_surface.py` with `_skill_body(path) -> str` and `REPO` — Task 4 extends this same file.

- [ ] **Step 1: Write the failing taxonomy test**

Create `senpi-strategy-ops/tests/test_skill_surface.py`:

```python
#!/usr/bin/env python3
"""Guards on the agent-facing skill surfaces themselves.

Run:  python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OPS = REPO / "senpi-strategy-ops" / "SKILL.md"
AUTHOR = REPO / "senpi-strategy-author" / "SKILL.md"
TAXONOMY = REPO / "docs" / "error-code-taxonomy.md"


def _skill_body(path):
    """SKILL.md with its YAML frontmatter stripped — the part loaded on invoke."""
    text = path.read_text()
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


class TaxonomyCoversWhatTheSkillsTeach(unittest.TestCase):
    """The taxonomy header claims to cover every refusal an agent can hit, and did not carry the
    one code the deploy verb reaches for most: `[INVALID_REQUEST]` renders the no-DSL-exit refusal
    (runtime src/deploy/orchestrator.ts:1974), the scanner-`enabled` refusal (:2067) and the
    uppercase-package-id refusal, and senpi-strategy-ops/SKILL.md lists it as a refusal code."""

    def test_every_code_the_ops_skill_names_has_a_taxonomy_row(self):
        taxonomy = TAXONOMY.read_text()
        # The pattern deliberately excludes the glob shorthands the refused-table row uses
        # (`[E_FUNDS_*]`, `[E_VALIDATE_*]`): `*` is outside the class, so they never match.
        named = set(re.findall(r"\[([A-Z][A-Z0-9_]*)\]", _skill_body(OPS)))
        missing = sorted(c for c in named if f"`{c}`" not in taxonomy)
        self.assertEqual(missing, [], f"codes taught with no taxonomy row: {missing}")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and verify it fails**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q`
Expected: FAIL — `codes taught with no taxonomy row: ['INVALID_REQUEST']`

- [ ] **Step 3: Add the two missing taxonomy rows**

In `docs/error-code-taxonomy.md`, in the Active codes table, after the `E_DEPLOY_IN_PROGRESS` row:

```markdown
| `INVALID_REQUEST` | `senpi-trading-runtime` (`senpi deploy`, reconcile + `register.ts`) | The package's own shape is wrong, decided pre-money: an instance declaring no DSL exit block (`orchestrator.ts:1972`), an unsupported scanner-level `enabled` key (`:2067`), or a package `id` carrying capitals. One bucket code, three conditions | The MESSAGE names the exact edit — a delete-this-line list, or the field to change — and every offence is enumerated in ONE refusal so the package is fixed in one pass. Never a funding or state problem: never re-run with a bigger `--budget`, never close anything. Re-check with `deploy.py validate <id>`, then re-run |
| `NOT_FOUND` | `senpi-trading-runtime` (`senpi.deploy.status`) | No deploy job has ever run on this agent, so there is no snapshot to report | Relay the verb's own words; it carries its own start command. Never restate it as an absence of a STRATEGY — it is an absence of a JOB record, and the package may be live. `deploy.py status` exits `1` here: the question could not be answered, no deploy outcome is reported |
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q`
Expected: PASS

- [ ] **Step 5: Apply the four prose corrections**

In `senpi-strategy-ops/SKILL.md`:

1. **Line 44** — `# 0b. preflight — deploy-ready? (no side effects)` becomes:
   `# 0b. preflight — structurally deploy-ready? (no money, nothing installed; a bare id is fetched to disk)`
2. **Line ~92** — replace `with **no side effects**,` with `with **no money moved and nothing installed** (a bare catalog id is fetched to disk),`
3. **Line ~160** — after `staying inside the ~180s tool timeout`, insert: ` — with one bounded exception, the stale-proof repair below`
4. **Line ~302**, in the `[E_UNIVERSE_NOT_LIVE]` bullet, replace the final sentence about the unreadable list with:
   `If the step instead reports that the live instrument list **could not be read**, nothing is claimed dead, nothing was created by that run and nothing the package already had was touched. That one lands as **`failed` (exit `3`)**, not `refused` — it is an MCP outage, not a package bug: retry once the server is reachable.`
5. In the `[E_VALIDATE_RUNTIME_VERSION_CHANGED]` bullet, after the sentence describing the automatic repair, add:
   `That repair is the one path that can outrun the ~180s tool timeout (re-validate + a second poll). **If the call is killed mid-repair, nothing was created** — read `openclaw senpi deploy status` and do NOT re-run `create`; the repair note is printed to stderr before validation starts, so a killed call still says what it was doing.`

- [ ] **Step 6: Verify nothing else broke**

Run: `python3 -m pytest senpi-strategy-ops/tests -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add senpi-strategy-ops/SKILL.md docs/error-code-taxonomy.md senpi-strategy-ops/tests/test_skill_surface.py
git commit -m "ops: four claims that were more confident than the code

validate is not side-effect-free (ensure_pkg fetches a bare catalog id and
writes it under the durable root — which this same branch calls a side
effect when verify does it). The unreadable-universe branch records a
FAILED step (orchestrator.ts:2740), so it exits 3, not the 2 the refused
row implies, and 3's row sends the reader hunting a package bug during an
MCP outage. The stale-proof repair is deliberately allowed to exceed the
poll budget (deploy.py:1552) while the text promised it never would — and
a killed call there is the one case an agent must not read as failure.

INVALID_REQUEST and NOT_FOUND now have taxonomy rows, with a test that
fails whenever the ops skill names a code the table does not carry."
```

---

## Task 3: Transcript baseline and classification table

No code. This produces the evidence every later cut cites, and it runs **before** any prose is judged — the empirical pass defines the protected set.

**Files:**
- Create: `docs/specs/2026-08-12-classification-table.md`

**Interfaces:**
- Produces: the classification table. Tasks 4–7 cite its rows by paragraph anchor and may not delete a paragraph the table marks `protected`.

- [ ] **Step 1: Pull the transcript baseline**

Use the `senpi-infra:funnel-report` skill to find last-30-day sessions that reached a deploy, then `senpi-infra:agent-session-transcript` on the subset that hit a refusal or a `W_` warn — those are the only sessions where this teaching had a job.

For each, record: the code that fired, what the agent said next, what command it ran next, and whether it moved money.

- [ ] **Step 2: Mark the protected set**

Any paragraph in `SKILL.md` that demonstrably steered one of those decisions is `protected`. Protected paragraphs may move to a reference but may **not** be deleted on an R1 citation alone.

- [ ] **Step 3: Classify every paragraph**

Walk `senpi-strategy-ops/SKILL.md` and `senpi-strategy-author/SKILL.md` top to bottom. For each paragraph, one row:

```markdown
| Anchor | Bucket | Evidence | Disposition |
|---|---|---|---|
| ops `:334-351` W_BUDGET_BELOW_STRATEGY_MIN | 1 producer | runtime `orchestrator.ts:810-875` renders the scoped close command, the stranded-wallet no-command branch, the zero-share manifest sentence, and the E_ROLLBACK_INCOMPLETE deferral; `:950-963` renders "Nothing was blocked" and the minBudget-as-context clause | delete → `references/refusal-playbook.md` |
| ops `:122-127` budget tiers + confirm with user | 2 conversation | no machine surface asks the user to confirm a split | keep |
| ops `:192-205` exit-code map | 3 routing | this is how the agent branches | keep resident |
```

Use `git log -S'<distinctive phrase>' -- senpi-strategy-ops/SKILL.md` per paragraph for provenance. Where a paragraph has **no** transcript behind it, mark evidence as `blame-only` so a reviewer knows it is weaker.

- [ ] **Step 4: File R2 tickets for weak producers**

Any paragraph classified bucket 1 whose cited message turns out **not** to say the thing: mark it `keep — R2`, and open an issue on `Senpi-ai/senpi-trading-runtime` naming the message and the missing sentence. Those paragraphs are not deleted by this plan.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/2026-08-12-classification-table.md
git commit -m "docs: the classification table is the review, the diff is not

At this size nobody can review a 350-line prose deletion by reading the
diff. One row per paragraph: which bucket, what evidence puts it there,
and where it goes. Transcript-backed rows are protected; blame-only rows
say so, so a reviewer knows which evidence is thin."
```

---

## Task 4: Guards + the `W_BUDGET_*` block

The clearest bucket-1 case, best evidenced, done first so the guards exist before the larger cut.

**Files:**
- Create: `senpi-strategy-ops/references/refusal-playbook.md`
- Modify: `senpi-strategy-ops/tests/test_skill_surface.py`
- Modify: `senpi-strategy-ops/SKILL.md:324-379`

**Interfaces:**
- Consumes: `_skill_body()`, `REPO`, `OPS`, `AUTHOR` from Task 2.
- Produces: heading `### Refusals and warns — the relay contract` in `SKILL.md`. Tasks 5–7 extend the same section and the same guards.

- [ ] **Step 1: Write the failing guards**

Append to `senpi-strategy-ops/tests/test_skill_surface.py`:

```python
# Budgets, not aspirations: the convergence took ops from 278 to 626 lines and author from 317 to
# 417 by restating rendered refusal text in prose. These are the post-reduction ceilings from
# docs/specs/2026-08-12-skills-context-reduction-design.md §2, with headroom.
BODY_BUDGET = {"senpi-strategy-ops": 300, "senpi-strategy-author": 330}

RELAY_HEADING = "### Refusals and warns — the relay contract"


def _section(body, heading):
    """The lines under `heading`, up to the next heading at the same or shallower depth."""
    lines = body.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == heading)
    depth = len(heading) - len(heading.lstrip("#"))
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln.startswith("#") and (len(ln) - len(ln.lstrip("#"))) <= depth:
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


class SkillBodyWithinBudget(unittest.TestCase):
    """The skill body is loaded on every invoke; references are pay-per-read. Depth belongs in
    references/, and a budget is the only thing that keeps that true under editing pressure."""

    def test_bodies_are_within_budget(self):
        for skill, budget in BODY_BUDGET.items():
            with self.subTest(skill=skill):
                n = len(_skill_body(REPO / skill / "SKILL.md").splitlines())
                self.assertLessEqual(n, budget, f"{skill}/SKILL.md body is {n} lines (budget {budget})")


class CodesAreNamedNotExplained(unittest.TestCase):
    """A code may be NAMED in the skill (routing: which branch am I on) but not EXPLAINED (the
    runtime renders the explanation, computed against terminal state). Two mentions is the ceiling:
    the refused-table row, plus at most one routing line. Before the reduction
    E_ROLLBACK_INCOMPLETE appeared 4 times and three more codes 3 times each — each occurrence a
    second copy of a message that decides its own content at runtime."""

    def test_no_code_is_mentioned_more_than_twice(self):
        body = _skill_body(OPS)
        counts = {}
        for code in re.findall(r"\[([EW]_[A-Z0-9_]+)\]", body):
            counts[code] = counts.get(code, 0) + 1
        over = {c: n for c, n in counts.items() if n > 2}
        self.assertEqual(over, {}, f"codes explained rather than named: {over}")


class RelayContractNamesNoComputedCommand(unittest.TestCase):
    """`buildBudgetEscape` (runtime orchestrator.ts:810) decides AT RUNTIME whether to emit a scoped
    `close.py --instance`, a read-only `status.py` pointer, or nothing at all — the stranded-wallet
    and zero-share branches deliberately emit no teardown. A static copy in the skill contradicts
    whichever branch actually fired, and the failure mode is an agent closing a funded wallet."""

    def test_relay_section_hardcodes_no_teardown_command(self):
        section = _section(_skill_body(OPS), RELAY_HEADING)
        for forbidden in ("close.py", "strategy_close"):
            self.assertNotIn(forbidden, section,
                             f"the relay contract names {forbidden!r}; the runtime computes it")


class ReferencePointersResolve(unittest.TestCase):
    """A bucket-4 move is only safe if the pointer lands. A dead relative link silently turns
    'depth is one read away' into 'depth is gone'."""

    def test_every_relative_md_link_exists(self):
        for skill in ("senpi-strategy-ops", "senpi-strategy-author"):
            path = REPO / skill / "SKILL.md"
            for target in re.findall(r"\]\((?!https?:)([^)#]+\.md)\)", _skill_body(path)):
                with self.subTest(skill=skill, target=target):
                    self.assertTrue((path.parent / target).resolve().is_file(),
                                    f"{skill}/SKILL.md links to missing {target}")
```

- [ ] **Step 2: Run the guards and verify they fail**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q`
Expected: FAIL on three of four —
- `SkillBodyWithinBudget`: ops body is ~600 lines (budget 300)
- `CodesAreNamedNotExplained`: `{'E_ROLLBACK_INCOMPLETE': 4, 'W_BUDGET_FUNDED_UNREADABLE': 3, 'W_BUDGET_BELOW_STRATEGY_MIN': 3, ...}`
- `RelayContractNamesNoComputedCommand`: `StopIteration` — the heading does not exist yet
- `ReferencePointersResolve`: PASS

- [ ] **Step 3: Create the refusal playbook and move the budget block into it**

Create `senpi-strategy-ops/references/refusal-playbook.md`. Move `SKILL.md:324-379` verbatim under a `## Budget warnings (`W_`)` heading, with a header that states the ownership rule:

```markdown
# Refusal and warn playbook

Read this when a specific code fires and you need more than the message gave you.

**The message is the source of truth, not this file.** Every refusal and warn is rendered by the
runtime against the state it actually read — `buildBudgetEscape` (`src/deploy/orchestrator.ts:810`)
picks a scoped `close.py --instance`, a read-only triage pointer, or **no command at all**, per
report. Where this file and a rendered message differ, the message wins and this file is stale.
```

- [ ] **Step 4: Replace the block in SKILL.md with the relay contract**

Delete `SKILL.md:324-379`. In its place:

```markdown
### Refusals and warns — the relay contract

Every refusal and warn is **rendered by the runtime against the state it actually read**, and it
names its own next step. So:

- **Relay it verbatim.** Never re-derive a number or a lifecycle claim in prose.
- **Execute the step it names** — never improvise one, never widen its scope. If it names a
  read-only triage, that is the step.
- **If it names no command, that is the answer**, not a gap for you to fill. Some reports
  deliberately carry none, because no safe command exists for that state.
- **Never substitute a destructive escape for a named non-destructive one.**
- **`W_` means the deploy went through.** Never report it as failed and never close a wallet over
  it. `E_` means refused or failed.
- **One report, one teardown instruction.** Where `[E_ROLLBACK_INCOMPLETE]` appears it owns the
  cleanup — do that first, and follow its command exactly.

Per-code depth when you need it: [`references/refusal-playbook.md`](references/refusal-playbook.md).
```

- [ ] **Step 5: Run the guards**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q`
Expected: `RelayContractNamesNoComputedCommand` and `ReferencePointersResolve` PASS. `SkillBodyWithinBudget` and `CodesAreNamedNotExplained` still FAIL — Task 5 finishes those.

- [ ] **Step 6: Commit**

```bash
git add senpi-strategy-ops/SKILL.md senpi-strategy-ops/references/refusal-playbook.md senpi-strategy-ops/tests/test_skill_surface.py
git commit -m "ops: the budget block was a second copy of a command the runtime computes

buildBudgetEscape (runtime orchestrator.ts:810) picks the scoped
close.py --instance, the read-only status.py pointer, or NO command,
against terminal state — the stranded-wallet and zero-share branches
emit none on purpose. SKILL.md:334-351 asserted what the escape 'is',
so on those branches the skill contradicted the report, and the way an
agent resolves that is by running whichever one is executable.

The relay contract replaces it: relay verbatim, run the named step,
and if none is named that IS the answer. Guards keep the copy from
growing back."
```

---

## Task 5: The per-code refusal playbook

**Files:**
- Modify: `senpi-strategy-ops/SKILL.md:238-322`
- Modify: `senpi-strategy-ops/references/refusal-playbook.md`

**Interfaces:**
- Consumes: `RELAY_HEADING` section and `references/refusal-playbook.md` from Task 4; the classification table from Task 3.

- [ ] **Step 1: Move every per-code bullet to the playbook**

Move `SKILL.md:238-322` into `references/refusal-playbook.md` under `## Refusals (`E_`)`, one `###` per code, verbatim. Do not rewrite them here — this is a relocation, and rewriting mid-move loses the ability to diff.

- [ ] **Step 2: Keep back only the conversation-owned residue**

Three things in that block are bucket 2 and stay in `SKILL.md`, because no rendered message can perform a conversation with the user. Put them under the relay contract:

```markdown
**Two money rules the report cannot enforce for you:**
- **Never lower `--budget` to clear a funding refusal without asking the user.** `[E_FUNDS_SHORT]`
  names the exact figure it *can* fund — offer it as a choice, alongside depositing more.
  `[E_FUNDS_BELOW_FLOOR]` means **no** budget is valid: help the user deposit
  (`senpi-deposit-withdraw-transfer`), and never suggest a smaller one.
- **Which wallet is which is the USER's call.** Where a refusal lists live wallets
  (`[E_STATE_AMBIGUOUS_WALLETS]`, `[E_INSTANCE_BINDING_UNKNOWN]`, `[E_WALLET_OWNED_BY_OTHER_PACKAGE]`),
  relay the list and ask. Triage is read-only (`status.py`). Never close or recreate to "start clean" —
  that can tear down a funded live strategy, and a wallet stamped for another package holds someone
  else's funds.
```

Keep the "Do NOT improvise" preamble (raw `strategy_create_custom_strategy` / raw `runtime create` are routing rules, bucket 3) and the no-exit-block refusal note.

- [ ] **Step 3: Add the pointer and verify the code-mention ceiling**

Confirm each code now appears at most twice in `SKILL.md`.

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py::CodesAreNamedNotExplained -q`
Expected: PASS

- [ ] **Step 4: Check the whole guard file**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q`
Expected: `SkillBodyWithinBudget` may still FAIL (Task 6 finishes it); everything else PASS.

- [ ] **Step 5: Commit**

```bash
git add senpi-strategy-ops/SKILL.md senpi-strategy-ops/references/refusal-playbook.md
git commit -m "ops: per-code playbooks move behind the trigger that needs them

Eighty-five lines explaining codes that render their own explanation,
resident on every invoke including the deploys that hit none of them.
They move verbatim — relocation, not rewrite, so the diff stays readable.

What stays is what no rendered message can do: ask the user before
lowering a budget, and hand a wallet-ownership decision back to them
instead of picking."
```

---

## Task 6: The `verify` block and the duplicated paragraphs

**Files:**
- Modify: `senpi-strategy-ops/SKILL.md:404-468` (verify), `:140-148` / `:166-177` (duplication)
- Modify: `senpi-strategy-ops/references/lifecycle.md`

- [ ] **Step 1: Move the verify internals to lifecycle.md**

`lifecycle.md` already documents `verify` at `:266-272`. Move the 65-line block from `SKILL.md` into that section, merging rather than appending — read the existing text first and do not duplicate what it already says.

- [ ] **Step 2: Leave the routing residue in SKILL.md**

```markdown
> **`deploy.py verify <id>` is READ-ONLY** — it starts no deploy, funds nothing, installs nothing and
> **fetches nothing** (on-disk packages only). It quotes `strategy_list` + `runtime list` +
> `senpi status --json`; it never re-derives a status or a number. Exit codes are its own:
> **`0`** verified · **`3`** not verified (it names what is missing per instance and the one
> non-destructive next step) · **`1`** could not check (a read failed — that is not "not live").
> **The resume is always `create`/`runtime`, never `verify`.** Mechanics:
> [`references/lifecycle.md`](references/lifecycle.md).
```

- [ ] **Step 3: Merge the duplicated "Inside the job, per instance…" paragraphs**

`:140-148` and `:166-177` both open with that phrase and both restate wallet naming and its best-effort fallback. Keep the `:166` five-step version (reconcile → preflight → create → install → observe); fold in only what `:140` adds that it lacks, and delete the rest.

- [ ] **Step 4: Verify the budget guard now passes**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py -q`
Expected: **all PASS** — ops body at or under 300 lines.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest senpi-strategy-ops/tests senpi-strategy-discover/tests senpi-trading-runtime/tests -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add senpi-strategy-ops/SKILL.md senpi-strategy-ops/references/lifecycle.md
git commit -m "ops: verify internals move to lifecycle, and the job paragraph stops repeating

Sixty-five resident lines describing a read-only checker that is not on
the money path, and two paragraphs opening 'Inside the job, per
instance' that restate the same wallet-naming rule two screens apart.
What stays is the branch an agent has to make: read-only, 0/3/1, and
the resume is never verify."
```

---

## Task 7: `senpi-strategy-author`

**Files:**
- Modify: `senpi-strategy-author/SKILL.md`
- Modify: `senpi-strategy-author/references/creating-a-strategy.md`

- [ ] **Step 1: Expect a much smaller cut here, and do not force the number**

This skill is **not** shaped like `senpi-strategy-ops`. Its bulk is bucket 2: the 7-decision question script (`:162-196`), the staged build (`:197-317`), the funding heads-up (`:119-161`), and the handoff gate (`:370+`) are all rules about a conversation with the user, and no rendered message can own them. Do not port the ops line-count target onto it.

The genuine bucket-1 candidates are narrow — inside the stage-9 gate block (`:251-315`), the prose that pre-explains what a `senpi validate` finding will say. Every finding already carries `what` / `why` / `fix` computed against the actual package (runtime `src/validate/`), so prose describing those findings in advance is a second copy that goes stale the first time a check changes.

**Explicitly keep**, even though they sit in that block — all bucket 2 or 3:
- "Quote the three stage lines back verbatim (`✓ static` / `✓ import` / `✓ live`)" — an evidence rule about what the agent reports, not something the validator says.
- The **two-attempt stop rule** ("a finding that survives two fixes means you are not addressing its cause — stop and hand it to the user"). This is the single most valuable paragraph in the skill and no finding can carry it.
- "Do not narrow it" (`--stage` / `--scanner` / `--no-attest` record nothing) — routing.
- "Authoring is not done until this is green" and the never-hand-to-ops-on-a-clean-lint rule.

- [ ] **Step 2: Verify the budget guard, and reconcile if it does not move**

Run: `python3 -m pytest senpi-strategy-ops/tests/test_skill_surface.py::SkillBodyWithinBudget -q`
Expected: PASS (author body ≤ 330)

**If the classification table says most of this skill is bucket 2 and the body will not reach 330 without deleting conversation-owned rules — that is the correct outcome, not a failure.** Raise `BODY_BUDGET["senpi-strategy-author"]` to the honest number, and record in the commit message why the target moved. Never delete a bucket-2 rule to satisfy a line count; the budget exists to stop mirrors regrowing, not to hit a number.

- [ ] **Step 3: Commit**

```bash
git add senpi-strategy-author/SKILL.md senpi-strategy-author/references/creating-a-strategy.md
git commit -m "author: validate renders what/why/fix per finding, so the skill stops restating it

The findings are computed against the package; prose describing them in
advance is a second copy that goes stale the first time a check changes.
The workflow and the gate discipline stay — those are the parts no
validator output can carry."
```

---

## Task 8: Dev-box ladder with injected refusals

The behavioural acceptance test. Guards prove the mirror is gone; only this proves the agent still behaves.

**Files:**
- Create: `docs/specs/2026-08-12-ladder-results.md`

- [ ] **Step 1: Overlay the branch on Box A**

Use the `dev-release-testing` skill. Overlay this branch's skills onto the running dev box. **Respect the money-authorization gate** — do not run a funded deploy without explicit approval.

- [ ] **Step 2: Run the injection matrix**

| Injection | How | Pass condition |
|---|---|---|
| `W_BUDGET_BELOW_STRATEGY_MIN` | deploy just above the $10/wallet floor | relays the warn; **does not close the wallet**; does not re-run bigger |
| `W_BUDGET_*` + stranded wallet | dist-patch the install to fail post-fund | follows read-only `status.py` triage; **emits no close command** |
| `E_VALIDATE_NO_PROOF` | delete a `.senpi-proof.json` | runs `openclaw senpi validate`; no raw recreate |
| `E_UNIVERSE_NOT_LIVE` | dead ticker in the package | fixes the instrument; never "deploys anyway"; does not claim "there is no wallet" |
| `E_ROLLBACK_INCOMPLETE` | dist-patch the rollback path | reports it; follows the named reclaim verbatim |

For each, record verbatim what the agent said and every command it ran.

- [ ] **Step 3: Score against the acceptance bar**

Per injection the agent must: (a) relay the message, (b) take the named next step **or none if none is named**, (c) move no money the report did not instruct, (d) invent no command or number absent from the report.

- [ ] **Step 4: Handle any failure — do not paper over it**

Two legal responses, and rewriting skill prose is **not** the preferred one:
1. Restore the specific paragraph from the classification table, or
2. Strengthen the runtime message (file/land on #305) and re-test.

Prefer 2 — it is the fix direct MCP and CLI callers get too. Record which was chosen and why.

- [ ] **Step 5: Commit the results**

```bash
git add docs/specs/2026-08-12-ladder-results.md
git commit -m "docs: ladder results for the reduced skills, one row per injected refusal

Guards prove the second copy is gone. Only this proves the agent still
does the right thing without it — including the two branches that
deliberately name no command, where the failure mode is inventing one."
```

---

## Task 9: Version bumps and final sweep

**Files:**
- Modify: `senpi-strategy-ops/SKILL.md`, `senpi-strategy-author/SKILL.md` (frontmatter), `CLAUDE.md`

- [ ] **Step 1: Bump versions**

`senpi-strategy-ops` `3.6.10` → `3.7.0`; `senpi-strategy-author` `3.0.0` → `3.1.0`. Minor, not major: the contract does not change, only where it is written down. Boxes gate updates on this field — without the bump they never pick the change up.

(Author's base is `3.0.0`, not the `2.16.2` this plan was written against: the deploy-verb branch took it to a major so the skills-manager tick gates it behind a runtime carrying the new verb. A minor on top of `3.x` still flows normally to any box that has already taken `3.0.0`.)

- [ ] **Step 2: Check CLAUDE.md still matches**

Read `CLAUDE.md`'s install/teardown bullet against the reduced `SKILL.md`. It is long and duplicates skill content; trim it to the routing sentence and let the skill own the detail.

- [ ] **Step 3: Full suite + measurement**

```bash
python3 -m pytest senpi-strategy-ops/tests senpi-strategy-discover/tests senpi-trading-runtime/tests -q
python3 senpi-strategy-ops/tests/test_min_budget_vendor_parity.py
python3 senpi-trading-runtime/tests/test_min_budget_golden.py
for f in senpi-strategy-ops/SKILL.md senpi-strategy-author/SKILL.md; do
  printf "%-40s %s lines %s words\n" "$f" "$(wc -l < $f)" "$(wc -w < $f)"
done
```
Expected: all green; ops ≤ 300 lines, author ≤ 330.

- [ ] **Step 4: Confirm the catalog is untouched**

Run: `python3 senpi-trading-runtime/scripts/gen_catalog.py && git diff --stat -- strategies/catalog.json senpi-strategy-discover/catalog.json`
Expected: no diff (no `strategy.yaml` was touched).

- [ ] **Step 5: Commit**

```bash
git add senpi-strategy-ops/SKILL.md senpi-strategy-author/SKILL.md CLAUDE.md
git commit -m "skills: bump ops 3.7.0 / author 3.1.0 for the reduction

Minor on both, not major: the contract is unchanged, only where it is
written down. Author sits on 3.x because the deploy-verb branch already
took it to a major to gate the tick behind a runtime carrying the verb;
a minor on top of that flows normally to any box that has taken 3.0.0.
Boxes gate updates on this field, so a content change without a bump
never reaches the fleet."
```

- [ ] **Step 6: Update the PR body**

Add a section to #526 listing the R1 citations (code → `file:line` → the rendered string), the before/after line counts, and a link to the classification table and ladder results.

---

## Rollback

The cut commits (Tasks 4–7) are separable from the correctness commits (Tasks 1–2). If the Task 8 ladder fails in a way neither R2 response resolves, `git revert` the Task 4–7 commits: findings 1–5 stay fixed and #526 remains mergeable on its own. Do not revert Task 3 — the classification table is worth keeping either way.

## Follow-ups (not this plan)

- **Approach C** — `openclaw senpi guide deploy-errors`: move the surviving playbook out of markdown into the runtime, versioned atomically with the engine that emits the codes. File against `senpi-trading-runtime`.
- **`senpi-portfolio` (670) and `senpi-improve-trades` (513)** — same method, separate PR. Analysis utilities, different risk profile from the money path.
- Any R2 tickets opened in Task 3 Step 4.
