"""Goldens for tests/context_budget/openclaw_measure.py, produced by the original OpenClaw code.

Estimator: every value below is what OpenClaw 2026.10.4 `estimateStringChars`
(packages/normalization-core/src/cjk-chars.ts, run under node 24 with type stripping) returned for
the same string. Catalog: the expected block is what openclaw@2026.5.7 `formatSkillsForPrompt`
(dist/workspace-B4eaH2KK.js) returned, and the frontmatter cases are what its `parseFrontmatterBlock`
(dist/frontmatter-BfEx-kV3.js, yaml@2.8.4) returned. Synthetic strings only: CI checkouts are
shallow, so nothing here reads git history. Regenerate on any OpenClaw bump.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_budget"))
import openclaw_measure as om  # noqa: E402


def _range_cases():
    # For every weighted range: the code points just outside, both ends and the midpoint, wrapped
    # in ASCII ("x" + cp + "y"), so each case checks one lookup plus the UTF-16 length.
    cases = {}
    for group, ranges in enumerate(om.WEIGHTED_CJK_RANGES, 1):
        for start, end in ranges:
            for cp in sorted({start - 1, start, (start + end) // 2, end, end + 1}):
                cases[f"g{group}_{cp:05x}"] = "x" + chr(cp) + "y"
    return cases


MISC_CASES = {
    "empty": "",
    "ascii": "hello, world! <tag> & 'q'",
    "middot": "a · b · c",
    "middot_run": "·" * 7,
    "latin1": "café naïve — “quotes” …",
    "emoji_unweighted": "ok \U0001F600 \U0001F680 done",
    "emoji_weighted_1f200": "\U0001F200\U0001F2FF",
    "surrogate_pair_cjk_ext_b": "\U00020000\U0002A6D6\U0002FA1F\U0002FA20",
    "lone_high": "a\ud83dz",
    "lone_low": "a\ude00z",
    "lone_high_at_end": "abc\ud840",
    "high_then_high_low": "\ud840𠀀",
    "three_token": "\U0001D360\U0001D371\U0001D372",
    "mixed_all": "中文 한국어 ㄱ ꀀ ﹐ ｡ ￠ \U0001D365 \U00016FE0 · ˇ ̅ ̣ ascii",
    "cjk_paragraph": "今日は良い天気です。" * 20,
    "control_and_nul": "a\x00b\x7f\x80c",
}

# estimateStringChars under node, per case name.
EXPECTED = {
    "g1_000b6": 3, "g1_000b7": 6, "g1_000b8": 3, "g1_02fff": 14, "g1_03000": 6, "g1_030cf": 6,
    "g1_0319f": 6, "g1_031a0": 14, "g1_04dff": 14, "g1_04e00": 6, "g1_076d2": 6, "g1_09fa5": 6,
    "g1_09fa6": 14, "g1_0abff": 3, "g1_0ac00": 6, "g1_0c1d7": 6, "g1_0d7af": 6, "g1_0d7b0": 14,
    "g1_0ff00": 3, "g1_0ff01": 6, "g1_0ff30": 6, "g1_0ff60": 6, "g1_0ff61": 10,
    "g2_010ff": 3, "g2_01100": 14, "g2_0117f": 14, "g2_011ff": 14, "g2_01200": 3, "g2_02e7f": 3,
    "g2_02e80": 14, "g2_02f3f": 14, "g2_02fff": 14, "g2_03000": 6, "g2_0319f": 6, "g2_031a0": 14,
    "g2_03fcf": 14, "g2_04dff": 14, "g2_04e00": 6, "g2_09fa5": 6, "g2_09fa6": 14, "g2_09fd2": 14,
    "g2_09fff": 14, "g2_0a000": 14, "g2_0a27f": 14, "g2_0a4ff": 14, "g2_0a500": 3, "g2_0a6ff": 3,
    "g2_0a700": 14, "g2_0a703": 14, "g2_0a707": 14, "g2_0a708": 3, "g2_0a95f": 3, "g2_0a960": 14,
    "g2_0a96f": 14, "g2_0a97f": 14, "g2_0a980": 3, "g2_0d7af": 6, "g2_0d7b0": 14, "g2_0d7d7": 14,
    "g2_0d7ff": 14, "g2_0d800": 3, "g2_0f8ff": 3, "g2_0f900": 14, "g2_0f9ff": 14, "g2_0faff": 14,
    "g2_0fb00": 3,
    "g3_002c6": 3, "g3_002c7": 10, "g3_002c8": 3, "g3_002c9": 10, "g3_002ca": 10, "g3_002cb": 10,
    "g3_002cc": 3, "g3_002d8": 3, "g3_002d9": 10, "g3_002da": 3, "g3_002e9": 3, "g3_002ea": 10,
    "g3_002eb": 10, "g3_002ec": 3, "g3_00304": 3, "g3_00305": 10, "g3_00306": 3, "g3_00322": 3,
    "g3_00323": 10, "g3_00324": 3, "g3_0fe0f": 3, "g3_0fe10": 10, "g3_0fe2f": 10, "g3_0fe4f": 10,
    "g3_0fe50": 3, "g3_0ff60": 6, "g3_0ff61": 10, "g3_0ff9e": 10, "g3_0ffdc": 10, "g3_0ffdd": 3,
    "g3_0ffdf": 3, "g3_0ffe0": 10, "g3_0ffe3": 10, "g3_0ffe6": 10, "g3_0ffe7": 3,
    "g4_1d35f": 4, "g4_1d360": 14, "g4_1d368": 14, "g4_1d371": 14, "g4_1d372": 4,
    "g5_16fdf": 4, "g5_16fe0": 18, "g5_16fef": 18, "g5_16fff": 18, "g5_17000": 4, "g5_1afef": 4,
    "g5_1aff0": 18, "g5_1aff7": 18, "g5_1afff": 18, "g5_1b000": 18, "g5_1b0b7": 18, "g5_1b16f": 18,
    "g5_1b170": 4, "g5_1f1ff": 4, "g5_1f200": 18, "g5_1f27f": 18, "g5_1f2ff": 18, "g5_1f300": 4,
    "g5_1ffff": 4, "g5_20000": 18, "g5_27d0f": 18, "g5_2fa1f": 18, "g5_2fa20": 4, "g5_2ffff": 4,
    "g5_30000": 18, "g5_31a3f": 18, "g5_3347f": 18, "g5_33480": 4,
    "empty": 0, "ascii": 25, "middot": 15, "middot_run": 28, "latin1": 23, "emoji_unweighted": 13,
    "emoji_weighted_1f200": 32, "surrogate_pair_cjk_ext_b": 50, "lone_high": 3, "lone_low": 3,
    "lone_high_at_end": 4, "high_then_high_low": 17, "three_token": 26, "mixed_all": 127,
    "cjk_paragraph": 800, "control_and_nul": 6,
}

CASES = {**_range_cases(), **MISC_CASES}


def test_every_case_has_a_golden():
    assert sorted(CASES) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_estimate_matches_openclaw(name):
    assert om.estimate_string_chars(CASES[name]) == EXPECTED[name], name


def test_ascii_is_utf16_length():
    assert om.estimate_string_chars("a" * 1000) == 1000


CATALOG_SKILLS = [
    ("a-skill", "Use for \"x\" & 'y' <z> — ok · fine", "/data/.openclaw/skills/a-skill/SKILL.md"),
    ("b", "one line", "/data/.openclaw/skills/b/SKILL.md"),
]
# formatSkillsForPrompt(CATALOG_SKILLS) under node; its .length was 713.
CATALOG_EXPECTED = "\n\nThe following skills provide specialized instructions for specific tasks.\nUse the read tool to load a skill's file when the task matches its description.\nWhen a skill file references a relative path, resolve it against the skill directory (parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.\n\n<available_skills>\n  <skill>\n    <name>a-skill</name>\n    <description>Use for &quot;x&quot; &amp; &apos;y&apos; &lt;z&gt; \u2014 ok \u00b7 fine</description>\n    <location>/data/.openclaw/skills/a-skill/SKILL.md</location>\n  </skill>\n  <skill>\n    <name>b</name>\n    <description>one line</description>\n    <location>/data/.openclaw/skills/b/SKILL.md</location>\n  </skill>\n</available_skills>"


def test_catalog_block_matches_openclaw():
    block = om.format_skills_for_prompt(CATALOG_SKILLS)
    assert block == CATALOG_EXPECTED
    assert om.js_length(block) == 713
    assert om.format_skills_for_prompt([]) == ""


FRONTMATTER_CASES = {
    "folded_strip": (
        "---\nname: my-skill\ndescription: >-\n  First line of the\n  description, \"quoted\" & <tagged>.\n\n"
        "  Second paragraph · with a dot.\nmetadata:\n  version: \"1.0.0\"\n---\n# Body\n",
        ("my-skill", "First line of the description, \"quoted\" & <tagged>.\nSecond paragraph · with a dot."),
    ),
    "folded_more_indented": (
        "---\nname: x\ndescription: >\n  Lead line\n    more-indented line kept\n  back to normal\n---\nbody\n",
        ("x", "Lead line\n  more-indented line kept\nback to normal"),
    ),
    "plain_inline_quoted": (
        "---\nname: ' spaced '\ndescription: \"One line: with colon\"\n---\n",
        ("spaced", "One line: with colon"),
    ),
    "no_name_falls_back": (
        "---\ndescription: plain text here\n---\n",
        ("fallback-dir", "plain text here"),
    ),
    "crlf": (
        "---\r\nname: crlf-skill\r\ndescription: >-\r\n  wrapped\r\n  text\r\n---\r\n",
        ("crlf-skill", "wrapped text"),
    ),
}


@pytest.mark.parametrize("name", sorted(FRONTMATTER_CASES))
def test_frontmatter_entry_matches_openclaw(name, tmp_path):
    raw, (want_name, want_description) = FRONTMATTER_CASES[name]
    skill_dir = tmp_path / "fallback-dir"
    skill_dir.mkdir()
    with open(skill_dir / "SKILL.md", "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    assert om.skill_prompt_entry(str(skill_dir)) == (
        want_name, want_description, "/data/.openclaw/skills/fallback-dir/SKILL.md")


def test_skill_without_description_is_not_rendered(tmp_path):
    skill_dir = tmp_path / "nodesc"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: nodesc\n---\nbody\n", encoding="utf-8")
    assert om.skill_prompt_entry(str(skill_dir)) is None


# ---- 5.7 follow-up replay ------------------------------------------------------------------------
# Each case is (text, maxChars). maxChars None = replay_57 (the full truncateToolResultMessage path
# at the 16,000 live cap, as 5.7 replays a SKILL.md read); a number = truncateToolResultText(text,
# maxChars) with the default suffix and minKeepChars, to reach the small-budget branches.
_LINE = "Lorem ipsum dolor sit amet, consectetur adipiscing elit sed do.\n"  # 64 chars


def _prose(n):
    return (_LINE * (n // len(_LINE) + 1))[:n]


def _replay_cases():
    body = _LINE * 470  # 30,080 chars, ends on a newline
    return {
        "whole_under_cap": (_prose(15_999), None),
        "whole_at_cap": (_prose(16_000), None),
        "head_only_prose": (body, None),
        "head_only_no_newline": ("a" * 30_000, None),
        "head_only_far_newline": (_prose(10_000) + "b" * 25_000, None),
        "tail_total": (body + "Grand total: 5\n", None),
        "tail_totally_no_boundary": (body + "That is totally fine\n", None),
        "tail_upper_done": (body + "DONE.", None),
        "tail_glued_no_boundary": (_prose(30_000) + "DONE.", None),
        "tail_brace_ideographic_space": (body + "{\"a\": 1}\n 　", None),
        "tail_brace_bom": (body + "}﻿", None),
        "tail_error_outside_window": (body + "an error here\n" + _prose(2_500) + "\n", None),
        "tail_exit_code_inside": (body + "exit code 3\n" + _prose(1_500) + "\n", None),
        "tail_underscore_no_boundary": (body + "not_done_ yet _done\n", None),
        "tail_nonascii_boundary": (body + "édone\n", None),
        "tail_newline_snap_far": (body + "x" * 3_990 + "\nresult\n", None),
        "astral_at_head_cut": ("\U0001F600" * 20_000 + "summary", None),
        "astral_plain": ("ab\U0001F600" * 9_000, None),
        "lone_surrogates": (("a\ud83d" + _LINE) * 400 + "finished", None),
        "small_tail_branch_skipped": (body + "total", 3_000),
        "small_head_and_tail": (body + "total", 5_000),
        "small_head_and_tail_min": (body + "total", 4_100),
        "tiny_suffix_loop": (body, 40),
        "tiny_suffix_only": (body, 10),
        "tiny_under": ("short total", 40),
    }


REPLAY_CASES = _replay_cases()

# Under node with the original 5.7 functions (cut verbatim from dist/compaction-BxhK_yKO.js):
# name -> (hasImportantTail(text), output .length, sha256 of the output as UTF-16LE).
REPLAY_EXPECTED = {
    "whole_under_cap": (False, 15999, "408f58042c9f828caff351d1d57309eb2aeab40955ac870a091961813af112d3"),
    "whole_at_cap": (False, 16000, "f471eae2b1b3140636406827654575b8f5b23549e6d9ef113f884d69a32ecb2c"),
    "head_only_prose": (False, 15972, "83ff6331d0ea772eee942df78798742b33297417df55f25de68d9fb0b8b7b701"),
    "head_only_no_newline": (False, 16000, "91bf45503f9ffb0cdbbb18f325788a682ebace57e47c97869cffc5bdc51ea9c8"),
    "head_only_far_newline": (False, 16000, "e5854597aaa46fd88d30d550282cba56d0c1c0cda8d83e9b5dc41f3b2146ca9e"),
    "tail_total": (True, 15922, "fe50c7c2d61215ff2a718876bb6eb83f681e9db9a540a5ee31108aeb6c6c22dd"),
    "tail_totally_no_boundary": (False, 15972, "6e9f9602b87674e5568785b8bb94e683fd527b5da79cac4c2f3f6e1c178f4db9"),
    "tail_upper_done": (True, 15912, "446fea0dd12d0e2ff9c32bd1a3ce465fdcb13a8715c6960c90f5d7b0a0acf845"),
    "tail_glued_no_boundary": (False, 15972, "9cc21ac5981f8e7a91f5cda250547a3419917ff862086aabbb384bfbf5f10f3b"),
    "tail_brace_ideographic_space": (True, 15918, "599a69be3e32f9d436041ee5d5eb53f963aa1cf5a7ff0d46a972a3e078531db6"),
    "tail_brace_bom": (True, 15909, "81609bdc61cdac868e2251f777e9cf540fe37d4761af51bc2fab93b68af8a839"),
    "tail_error_outside_window": (False, 15972, "02251c3beebd93a29706163e934955ca08d8c1893d20ee233a6f622485d67733"),
    "tail_exit_code_inside": (True, 15884, "ae3c5e3a6c145d3cfbb2073bbf4bad5a3d44678cc55f0c45fc9a12849a51107a"),
    "tail_underscore_no_boundary": (False, 15972, "ae82b2cb54217e03f71c88af792b3c5e213a03c712a7c10bcf04498e888ec4a6"),
    "tail_nonascii_boundary": (True, 15913, "fd4ddfa6653560dd6188374a643946e39f0cbb8e70e856dd7953c3571c121e8b"),
    "tail_newline_snap_far": (True, 15937, "df3f911127a384d0f7c2b1ee6fb3fa38baa086079441ec8bd3267556648ad24e"),
    "astral_at_head_cut": (True, 16000, "121ec7e90e33e8d8037e593928b7dcc5dd2dba1197bf50d541765b147d7b0a0a"),
    "astral_plain": (False, 16000, "af51e0fc19fd5c815a5bf9b2bbb5dafdb683a4ae567e7cef5cfd392ec9e13160"),
    "lone_surrogates": (True, 15947, "fff8df9a569ed70e3b8b9a0ea209d939c1a103a47ebe7e00a56ebf704661b075"),
    "small_tail_branch_skipped": (True, 2980, "5be0ed829ba78a66671ac7aac84344124bde6c5f6a537a54de5b590292d9c5e4"),
    "small_head_and_tail": (True, 4968, "3cfd3f001f9ec306b2b8f04c642ed23003996871752ecd44c2affe5b3f4a7e45"),
    "small_head_and_tail_min": (True, 4008, "b5f36d482ac1ca616ddfa87c7b4cb703b54015661c2c1c70b2fc07f87723964f"),
    "tiny_suffix_loop": (False, 40, "aced71342dda1c8d8b4d9456ea49fbd0a512147c20cd46a39051be433f3c5376"),
    "tiny_suffix_only": (False, 10, "32281e553c4c60c090838e256a3f3580032092feaeb031c701e70e6e5354fa73"),
    "tiny_under": (True, 11, "7b8e44e401b01936781812a49a96bdb7236a0c14553f08ba8d31347b572b22ab"),
}


def _sha_utf16(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-16-le", "surrogatepass")).hexdigest()


def _replay(text, max_chars):
    return om.replay_57(text) if max_chars is None else om.truncate_tool_result_text(text, max_chars)


def test_every_replay_case_has_a_golden():
    assert sorted(REPLAY_CASES) == sorted(REPLAY_EXPECTED)


@pytest.mark.parametrize("name", sorted(REPLAY_EXPECTED))
def test_replay_matches_openclaw(name):
    text, max_chars = REPLAY_CASES[name]
    out = _replay(text, max_chars)
    important, length, sha = REPLAY_EXPECTED[name]
    assert om.has_important_tail(text) is important, name
    assert (om.js_length(out), _sha_utf16(out)) == (length, sha), name


def test_replay_layout_names_the_mode():
    assert om.replay_57_layout(REPLAY_CASES["whole_under_cap"][0]) == ("whole", 15_999, None)
    mode, head, tail = om.replay_57_layout(REPLAY_CASES["head_only_prose"][0])
    assert mode == "head-only" and 15_900 < head <= 16_000 and tail is None
    mode, head, tail = om.replay_57_layout(REPLAY_CASES["tail_total"][0])
    text = REPLAY_CASES["tail_total"][0]
    assert mode == "head+tail" and 11_000 < head < 12_000 and len(text) - tail < 4_000
    assert om.important_tail_trigger(text) == "total"
    assert om.important_tail_trigger(REPLAY_CASES["tail_brace_bom"][0]) == "}"
    assert om.important_tail_trigger(REPLAY_CASES["head_only_prose"][0]) is None
