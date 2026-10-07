#!/usr/bin/env python3
"""Every package YAML must parse the SAME under the vendored loader as under PyYAML.

Why this exists. `_pkg.load` uses PyYAML when the host has it and falls back to the vendored
stdlib loader `_yaml.py` otherwise — and agent hosts generally do NOT have PyYAML (no pip). So
every strategy.yaml and runtime.yaml is read by two different parsers depending on the box, and
until 2026-10-07 nothing compared them.

What that cost. The vendored loader could not parse a block sequence sitting at its parent key's
indent:

    instances:
    - name: harvest          # canonical YAML, and what `yaml.safe_dump` emits

It returned None for the key, then read `- name: harvest` as a SIBLING KEY `'- name'`, and
DROPPED every remaining key in the document. No error, no warning. On a PyYAML host the same
file read perfectly, so it was invisible to CI, to local runs, and to review.

Nine packages shipped that way — athena-concentrated, camel-concentrated, puffin-duo and all six
penguin/penguins-duo chase arms, i.e. every variant generated on 2026-10-06/07, because they were
written out programmatically while the 119 older manifests were hand-indented. They were
undeployable on any PyYAML-less host: `'instances' must be a non-empty list`. Four separate users
hit it over two days — three of them on the same package — before it was reported as "the package
has a structural issue". (Ids live in telemetry; this repo is public.)

Two more divergences surfaced from the same audit: a flow collection wrapped across lines raised
`unterminated flow sequence`, taking out grizzly-wild, vulture and hen outright; and a
single-quoted scalar kept its doubled-quote escape, so `camel''s` reached the user's card verbatim.

The guard is this parity test, not care. A hand-written manifest and a generated one must read
identically on both parsers, and only a comparison can promise that.

Note on `>`/`|` block scalars: the vendored loader folds them loosely (documented in `_yaml.py`),
so the comparison collapses runs of whitespace inside strings. That is exactly the equivalence the
one consumer relies on — `_cli._collapse` re-collapses `description` before comparing it. A
collapse cannot hide what this test is for: a dropped key, a None-ed list, or a changed number all
still fail.
"""
import glob
import importlib.util
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPTS = os.path.join(REPO, "senpi-strategy-ops", "scripts")
STRATEGIES = os.path.join(REPO, "strategies")


def _load(path, name):
    """Load a module BY PATH, without touching `sys.path` and without registering it as `_yaml`.

    Three sibling skills vendor `_yaml.py` and only senpi-improve-trades' copy defines `loads()`.
    `sys.path.insert(0, SCRIPTS)` here made `import _yaml` resolve to THIS copy for every test that
    ran after it in a shared pytest invocation, so `review.py`'s `_yaml.loads(...)` silently lost
    its registry and three improve-trades tests failed — but only in the combined run, never on
    their own. Import by path so this file cannot reach another suite.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_yaml = _load(os.path.join(SCRIPTS, "_yaml.py"), "_vendored_yaml_under_test")

try:
    import yaml as pyyaml
except ImportError:  # pragma: no cover — CI installs pyyaml in the first step
    pyyaml = None


def _collapse(x):
    """Whitespace-collapse every string, recursively. See the note on block scalars above."""
    if isinstance(x, str):
        return re.sub(r"\s+", " ", x).strip()
    if isinstance(x, dict):
        return {k: _collapse(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_collapse(v) for v in x]
    return x


def _package_yamls():
    files = sorted(glob.glob(os.path.join(STRATEGIES, "*", "strategy.yaml")))
    files += sorted(glob.glob(os.path.join(STRATEGIES, "*", "**", "runtime.yaml"), recursive=True))
    return files


# ---------------------------------------------------------------- the three regressions, named

def test_a_block_sequence_may_sit_at_its_parent_keys_indent():
    """The bug that made 9 packages undeployable. `safe_dump` emits exactly this shape."""
    doc = _yaml.safe_load(
        "instances:\n"
        "- name: harvest\n"
        "  runtime: harvest/runtime.yaml\n"
        "- name: payout\n"
        "  runtime: payout/runtime.yaml\n"
        "requires:\n"
        "  runtime: '>=3.0.0'\n"
    )
    assert isinstance(doc["instances"], list), f"instances parsed as {doc['instances']!r}"
    assert [i["name"] for i in doc["instances"]] == ["harvest", "payout"]
    assert doc["instances"][0]["runtime"] == "harvest/runtime.yaml"
    # the real damage was the SILENT truncation of everything after the sequence
    assert doc.get("requires") == {"runtime": ">=3.0.0"}, "keys after the sequence were dropped"
    assert "- name" not in doc, "a sequence item was read as a sibling mapping key"


def test_a_nested_block_sequence_may_sit_at_its_parent_keys_indent():
    doc = _yaml.safe_load(
        "catalog:\n"
        "  tags:\n"
        "  - carry\n"
        "  - basis\n"
        "  tier: advanced\n"
        "version: \"1.0.0\"\n"
    )
    assert doc["catalog"]["tags"] == ["carry", "basis"]
    assert doc["catalog"]["tier"] == "advanced", "keys after the nested sequence were dropped"
    assert doc["version"] == "1.0.0", "top-level keys after the nested sequence were dropped"


def test_a_flow_collection_may_wrap_across_lines():
    """grizzly-wild, vulture and hen raised `unterminated flow sequence` and were unusable."""
    doc = _yaml.safe_load('assets: ["HYPE", "HEMI",\n  "WLD", "MON"]\nafter: 1\n')
    assert doc["assets"] == ["HYPE", "HEMI", "WLD", "MON"]
    assert doc["after"] == 1
    assert _yaml.safe_load("cfg: {a: 1,\n  b: 2}\n")["cfg"] == {"a": 1, "b": 2}
    assert _yaml.safe_load("[1, 2,\n 3]\n") == [1, 2, 3]


def test_a_single_quoted_scalar_resolves_its_doubled_quote_escape():
    """`camel''s` is `camel's`; the literal `''` was reaching user-facing cards."""
    assert _yaml.safe_load("a: 'camel''s ladder'\n")["a"] == "camel's ladder"
    assert _yaml.safe_load("a: 'say ''hi'''\n")["a"] == "say 'hi'"
    assert _yaml.safe_load("a: camel's ladder\n")["a"] == "camel's ladder"


def test_indented_sequences_still_work():
    """The 119 hand-written manifests use the indented style — it must not regress."""
    doc = _yaml.safe_load(
        "instances:\n"
        "  - name: main\n"
        "    runtime: main/runtime.yaml\n"
        "requires:\n"
        "  runtime: '>=3.0.0'\n"
    )
    assert [i["name"] for i in doc["instances"]] == ["main"]
    assert doc["requires"] == {"runtime": ">=3.0.0"}
    assert _yaml.safe_load("a: []\nb: 1\n") == {"a": [], "b": 1}


# ---------------------------------------------------------------- the whole tree, both parsers

def test_the_vendored_loader_agrees_with_pyyaml_on_every_package_yaml():
    assert pyyaml is not None, (
        "PyYAML is required to run the parity guard — this is the test that proves every package "
        "reads the same on an agent host as it does here. Install it (pip install pyyaml); do not "
        "skip it.")
    files = _package_yamls()
    assert len(files) > 250, f"expected the whole tree, found only {len(files)} YAML files"
    bad = []
    for path in files:
        rel = os.path.relpath(path, REPO)
        text = open(path, encoding="utf-8").read()
        want = pyyaml.safe_load(text)
        try:
            got = _yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 — any raise here is the failure
            bad.append(f"{rel}: vendored loader RAISED {type(exc).__name__}: {exc}")
            continue
        if _collapse(got) != _collapse(want):
            bad.append(f"{rel}: vendored loader DISAGREES with PyYAML")
    assert not bad, (
        "the vendored loader reads these differently from PyYAML, so they behave differently on a "
        "host without PyYAML (most agent hosts) than they do in CI:\n  " + "\n  ".join(bad))


def test_every_multi_instance_package_keeps_its_instances_under_the_vendored_loader():
    """The deploy-fatal field, asserted on its own: `_pkg.load` raises BadPackage without it."""
    bad = []
    for path in sorted(glob.glob(os.path.join(STRATEGIES, "*", "strategy.yaml"))):
        sid = os.path.basename(os.path.dirname(path))
        text = open(path, encoding="utf-8").read()
        try:
            doc = _yaml.safe_load(text) or {}
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{sid}: RAISED {type(exc).__name__}: {exc}")
            continue
        flat = os.path.isfile(os.path.join(os.path.dirname(path), "runtime.yaml"))
        inst = doc.get("instances")
        if flat and not inst:
            continue  # a flat single-instance package synthesizes its entry
        if not isinstance(inst, list) or not inst:
            bad.append(f"{sid}: instances parsed as {inst!r} — deploy would raise BadPackage")
            continue
        for entry in inst:
            if not isinstance(entry, dict) or not entry.get("runtime"):
                bad.append(f"{sid}: instance entry missing 'runtime': {entry!r}")
    assert not bad, "undeployable on a host without PyYAML:\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------- the copies stay in sync

def test_the_three_vendored_copies_do_not_drift():
    """_yaml.py is vendored into three skills. senpi-improve-trades appends a public `loads()`
    wrapper; everything above it must be byte-identical, or one skill quietly keeps the bug."""
    ops = open(os.path.join(SCRIPTS, "_yaml.py"), encoding="utf-8").read()
    author = open(os.path.join(REPO, "senpi-strategy-author", "scripts", "_yaml.py"),
                  encoding="utf-8").read()
    improve = open(os.path.join(REPO, "senpi-improve-trades", "scripts", "_yaml.py"),
                   encoding="utf-8").read()
    assert author == ops, "senpi-strategy-author/_yaml.py DRIFTED from senpi-strategy-ops"
    marker = "\n\n\ndef loads(text):"
    assert marker in improve, "senpi-improve-trades/_yaml.py lost its loads() wrapper"
    assert improve[:improve.index(marker)].rstrip("\n") == ops.rstrip("\n"), (
        "senpi-improve-trades/_yaml.py DRIFTED from senpi-strategy-ops above its loads() wrapper")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"VENDORED YAML PARITY OK — {len(fns)} checks over {len(_package_yamls())} package YAMLs")
