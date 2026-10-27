"""The skill docs name the decision modes the runtime accepts, and do not offer `strategy.enabled` as a switch.

The runtime accepts exactly `llm`, `rule` and `none` for `decision_mode`. `none` never trades: every signal
is skipped with `execute: false`, which is how new entries are paused while DSL keeps managing open
positions. A name the runtime refuses, written into a doc, is copied into a recipe that does not load.
`strategy.enabled` is parsed but no runtime code reads it, so a doc that calls it "whether the runtime is
active" teaches a pause that pauses nothing. Text is matched with whitespace collapsed, so a rewrap cannot
break a check."""
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
REFS = os.path.join(HERE, "..", "references")
RUNTIME_YAML = os.path.join(REFS, "runtime-yaml.md")
RUNTIME_CONCEPTS = os.path.join(REFS, "runtime-concepts.md")


def _read(path):
    return open(path, encoding="utf-8").read()


def _flat(text):
    return " ".join(text.split())


def _section(text, heading):
    """The body under a `## ` heading, up to the next `## ` heading."""
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading):], re.M)
    return text[start: start + len(heading) + nxt.start()] if nxt else text[start:]


def _sentence_after(text, lead):
    """The text from `lead` to the end of its sentence."""
    flat = _flat(text)
    i = flat.index(lead)
    end = re.search(r"\.(\s|$)", flat[i:])
    return flat[i: i + end.end()] if end else flat[i:]


def test_no_skill_doc_names_a_mode_the_runtime_refuses():
    hits = []
    for path in sorted(glob.glob(os.path.join(REPO, "senpi-*", "**", "*.md"), recursive=True)):
        for n, line in enumerate(_read(path).splitlines(), 1):
            if "no_decision" in line:
                hits.append(f"{os.path.relpath(path, REPO)}:{n}: {line.strip()}")
    assert not hits, "`no_decision` is not a decision_mode (use `none`):\n" + "\n".join(hits)


def test_the_actions_block_lists_none_among_the_modes():
    block = _section(_read(RUNTIME_YAML), "## `actions` block")
    sentence = _sentence_after(block, "`decision_mode` ∈")
    for mode in ("`rule`", "`llm`", "`none`"):
        assert mode in sentence, (mode, sentence)


def test_a_reference_says_none_never_trades():
    text = _flat(_read(RUNTIME_YAML)) + " " + _flat(_read(RUNTIME_CONCEPTS))
    assert re.search(r"`none`[^.]{0,80}never trades", text), \
        "no reference says what `decision_mode: none` does (the action never trades)"


def test_strategy_enabled_is_not_documented_as_a_switch():
    block = _section(_read(RUNTIME_YAML), "## `strategy` block")
    rows = [ln for ln in block.splitlines() if ln.startswith("| `enabled`")]
    assert len(rows) == 1, rows
    row = rows[0]
    assert "whether the runtime is active" not in row, row
    assert re.search(r"\bnot read\b", row), row
    assert re.search(r"\b(does not|doesn't|never) pause", row), row
