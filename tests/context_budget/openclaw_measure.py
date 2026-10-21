"""How OpenClaw measures skill text, ported to Python for the context-budget CI gate.

Two measurements, each a line-for-line port of the OpenClaw code that applies the limit:

1. ``estimate_string_chars`` — OpenClaw 2026.10.4 ``estimateStringChars``
   (``packages/normalization-core/src/cjk-chars.ts``). 10.x refuses a SKILL.md read whose
   estimate exceeds the model's cap, so SKILL.md / references sizes are counted this way.
   It is the UTF-16 length plus a surcharge per code point in five CJK-weighted range groups.

2. ``format_skills_for_prompt`` — OpenClaw 2026.5.7 ``formatSkillsForPrompt``
   (npm ``openclaw@2026.5.7``, ``dist/workspace-B4eaH2KK.js``, region
   ``src/agents/skills/skill-contract.ts``). ``applySkillsPromptLimits`` compares the plain
   JS ``.length`` (UTF-16 code units, no CJK surcharge) of this block against
   ``skills.limits.maxSkillsPromptChars`` and drops every description (compact format) when
   it does not fit. ``catalog_chars`` returns that ``.length``.

3. ``replay_57`` — what OpenClaw 2026.5.7 keeps of a tool result (a SKILL.md read) on every
   later turn: ``truncateToolResultMessage`` / ``truncateToolResultText`` / ``hasImportantTail``
   (``dist/compaction-BxhK_yKO.js``, region ``src/agents/pi-embedded-runner/tool-result-truncation.ts``)
   at the live cap ``resolveLiveToolResultMaxChars`` gives a SKILL.md read (16,000 chars). A file
   over the cap is cut to its first ~15,9xx chars, or, when its last 2,000 chars look like a
   result (``hasImportantTail``), to a head of ~11,8xx plus a tail of ~3,9xx.

Re-port all three on any OpenClaw bump; tests/test_openclaw_measure.py pins goldens produced by
running the original TypeScript/JavaScript under node.
"""
import math
import os
import re

import yaml

# --- 1. estimateStringChars (OpenClaw 2026.10.4) ------------------------------------------

CHARS_PER_TOKEN_ESTIMATE = 4

# WEIGHTED_CJK_RANGES, verbatim and in order. Group index + 1 is the CJK_CATEGORY bucket.
WEIGHTED_CJK_RANGES = (
    (  # 1: common
        (0x00B7, 0x00B7),
        (0x3000, 0x319F),
        (0x4E00, 0x9FA5),
        (0xAC00, 0xD7AF),
        (0xFF01, 0xFF60),
    ),
    (  # 2: rare BMP
        (0x1100, 0x11FF),
        (0x2E80, 0x2FFF),
        (0x31A0, 0x4DFF),
        (0x9FA6, 0x9FFF),
        (0xA000, 0xA4FF),
        (0xA700, 0xA707),
        (0xA960, 0xA97F),
        (0xD7B0, 0xD7FF),
        (0xF900, 0xFAFF),
    ),
    (  # 3: two-token
        (0x02C7, 0x02C7),
        (0x02C9, 0x02CB),
        (0x02D9, 0x02D9),
        (0x02EA, 0x02EB),
        (0x0305, 0x0305),
        (0x0323, 0x0323),
        (0xFE10, 0xFE4F),
        (0xFF61, 0xFFDC),
        (0xFFE0, 0xFFE6),
    ),
    ((0x1D360, 0x1D371),),  # 4: three-token supplementary
    (  # 5: supplementary
        (0x16FE0, 0x16FFF),
        (0x1AFF0, 0x1AFFF),
        (0x1B000, 0x1B16F),
        (0x1F200, 0x1F2FF),
        (0x20000, 0x2FA1F),
        (0x30000, 0x3347F),
    ),
)

# Surcharge per code point on top of its UTF-16 length, at minimumRawWeight = 1:
# common 4-1, rareBmp 4*3-1, twoToken 4*2-1, threeTokenSupplementary 4*3-2, supplementary 4*4-2.
_SURCHARGE = {
    1: CHARS_PER_TOKEN_ESTIMATE - 1,
    2: CHARS_PER_TOKEN_ESTIMATE * 3 - 1,
    3: CHARS_PER_TOKEN_ESTIMATE * 2 - 1,
    4: CHARS_PER_TOKEN_ESTIMATE * 3 - 2,
    5: CHARS_PER_TOKEN_ESTIMATE * 4 - 2,
}


def _category(code_point):
    # Ranges are disjoint, so the first hit is the TS lookup table's value.
    for bucket, ranges in enumerate(WEIGHTED_CJK_RANGES, start=1):
        for start, end in ranges:
            if start <= code_point <= end:
                return bucket
    return 0


def utf16_units(text):
    """The string as JS sees it: a list of UTF-16 code units (lone surrogates kept)."""
    data = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def estimate_string_chars(text):
    """Port of estimateStringChars(text) (minimumRawWeight 1).

    Walks UTF-16 code units exactly as the TS loop does: a high surrogate followed by a low
    surrogate is one code point (index advances by 2); a lone surrogate is looked up as its own
    unit value and falls in no range. Starting the walk at index 0 instead of the TS
    ``firstWeighted`` shortcut changes nothing: no weighted code point precedes it, and both
    walks split surrogate pairs identically.
    """
    units = utf16_units(text)
    total = len(units)
    i = 0
    n = len(units)
    while i < n:
        cp = units[i]
        if cp >= 0x80:
            if 0xD800 <= cp <= 0xDBFF and i + 1 < n and 0xDC00 <= units[i + 1] <= 0xDFFF:
                cp = 0x10000 + (cp - 0xD800) * 0x400 + units[i + 1] - 0xDC00
                i += 1
            bucket = _category(cp)
            if bucket:
                total += _SURCHARGE[bucket]
        i += 1
    return total


def estimate_file_chars(path):
    with open(path, encoding="utf-8") as f:
        return estimate_string_chars(f.read())


# --- 2. formatSkillsForPrompt (OpenClaw 2026.5.7) ------------------------------------------

# Where the senpi-agent box keeps managed skills (OPENCLAW_STATE_DIR=/data/.openclaw, skills-manager
# writes <state>/skills/<dir>). Not under $HOME, so compactSkillPaths leaves it absolute.
BOX_SKILLS_ROOT = "/data/.openclaw/skills"

_HEADER = [
    "\n\nThe following skills provide specialized instructions for specific tasks.",
    "Use the read tool to load a skill's file when the task matches its description.",
    "When a skill file references a relative path, resolve it against the skill directory (parent of"
    " SKILL.md / dirname of the path) and use that absolute path in tool commands.",
    "",
    "<available_skills>",
]


def escape_xml(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def format_skills_for_prompt(skills):
    """skills: iterable of (name, description, location). Returns the full-format block."""
    skills = list(skills)
    if not skills:
        return ""
    lines = list(_HEADER)
    for name, description, location in skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{escape_xml(name)}</name>")
        lines.append(f"    <description>{escape_xml(description)}</description>")
        lines.append(f"    <location>{escape_xml(location)}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def js_length(text):
    """JS String.prototype.length: UTF-16 code units."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _extract_frontmatter_block(content):
    # extractFrontmatterBlock: normalize newlines, require a leading ---, cut at the next "\n---".
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---"):
        return None
    end = normalized.find("\n---", 3)
    if end == -1:
        return None
    return normalized[4:end]


def _frontmatter_string(value):
    # coerceYamlFrontmatterValue: strings are trimmed, numbers/booleans stringified.
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value).strip()
    return None


def skill_prompt_entry(skill_dir):
    """(name, description, location) as loadSingleSkillDirectory builds it, or None if skipped.

    OpenClaw parses the frontmatter with the npm ``yaml`` package (core schema); our
    descriptions are plain/folded block scalars, which PyYAML reads identically (cross-checked
    against yaml@2.8 under node). A skill with no description is not loaded at all.
    """
    with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
        block = _extract_frontmatter_block(f.read())
    data = {}
    if block:
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, dict):
                data = parsed
        except yaml.YAMLError:
            data = {}
    dir_name = os.path.basename(os.path.normpath(skill_dir))
    name = _frontmatter_string(data.get("name")) or dir_name.strip()
    description = _frontmatter_string(data.get("description"))
    if not name or not description:
        return None
    return name, description, f"{BOX_SKILLS_ROOT}/{dir_name}/SKILL.md"


def render_catalog(skill_dirs):
    """The full-format <available_skills> block for these skill dirs, in OpenClaw's order
    (sorted by name; the order does not change the length)."""
    entries = [e for e in (skill_prompt_entry(d) for d in skill_dirs) if e]
    entries.sort(key=lambda e: e[0])
    return format_skills_for_prompt(entries)


def catalog_chars(skill_dirs):
    return js_length(render_catalog(skill_dirs))


_SKILL_DIR_RE = re.compile(r"^[^.]")


def repo_skill_dirs(repo_root):
    """Every top-level dir with a SKILL.md: the set the skills-manager can install."""
    out = []
    for name in sorted(os.listdir(repo_root)):
        path = os.path.join(repo_root, name)
        if _SKILL_DIR_RE.match(name) and os.path.isfile(os.path.join(path, "SKILL.md")):
            out.append(path)
    return out


# --- 3. Follow-up replay of a tool result (OpenClaw 2026.5.7) ------------------------------------
# JS strings are UTF-16: every length, index and slice below is in code units, as in the original.

# DEFAULT_MAX_LIVE_TOOL_RESULT_CHARS; resolveLiveToolResultMaxChars returns it for any context
# window over 13,333 tokens (floor(window * 0.3) * 4 is larger), which every fleet model has.
LIVE_TOOL_RESULT_MAX_CHARS = 16_000
MIN_KEEP_CHARS = 2_000
MIDDLE_OMISSION_MARKER = "\n\n\u26a0\ufe0f [... middle content omitted \u2014 showing head and tail ...]\n\n"

_ERROR_TAIL_RE = re.compile(
    r"\b(error|exception|failed|fatal|traceback|panic|stack trace|errno|exit code)\b", re.ASCII)
_RESULT_TAIL_RE = re.compile(r"\b(total|summary|result|complete|finished|done)\b", re.ASCII)
# String.prototype.trim(): WhiteSpace + LineTerminator.
_JS_WHITESPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009" \
                 "\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def _units_str(text):
    # One Python char per UTF-16 code unit (surrogates kept as lone chars), so len/slice/find
    # behave like the JS string methods.
    return "".join(map(chr, utf16_units(text)))


def _from_units(units):
    return units.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def _suffix(truncated_chars):
    # formatContextLimitTruncationNotice
    return f"[... {max(1, int(truncated_chars))} more characters truncated]"


def important_tail_trigger(text):
    """What makes hasImportantTail(text) true, or None: the matched trigger word, or "}" when the
    last 2,000 chars end in a closing brace."""
    tail = _from_units(_units_str(text)[-2000:]).strip(_JS_WHITESPACE).lower()
    m = _ERROR_TAIL_RE.search(tail)
    if m:
        return m.group(1)
    if tail.strip(_JS_WHITESPACE).endswith("}"):
        return "}"
    m = _RESULT_TAIL_RE.search(tail)
    return m.group(1) if m else None


def has_important_tail(text):
    return important_tail_trigger(text) is not None


def _append_bounded_suffix(kept, original_len, max_chars):
    # appendBoundedTruncationSuffix
    while True:
        final = kept + _suffix(max(1, original_len - len(kept)))
        if len(final) <= max_chars:
            return final
        if not kept:
            return final[:max_chars]
        overflow = len(final) - max_chars
        nxt = kept[:max(0, len(kept) - overflow)]
        kept = nxt if len(nxt) < len(kept) else kept[:-1]


def _truncate_units(u, max_chars, min_keep_chars):
    # truncateToolResultText on a code-unit string; minKeepChars already resolved.
    if len(u) <= max_chars:
        return u
    budget = max(min_keep_chars, max_chars - len(_suffix(max(1, len(u) - max_chars))))
    if has_important_tail(_from_units(u)) and budget > min_keep_chars * 2:
        tail_budget = min(math.floor(budget * 0.3), 4000)
        head_budget = budget - tail_budget - 63
        if head_budget > min_keep_chars:
            head_cut = head_budget
            head_newline = u.rfind("\n", 0, head_budget + 1)
            if head_newline > head_budget * 0.8:
                head_cut = head_newline
            tail_start = len(u) - tail_budget
            tail_newline = u.find("\n", tail_start)
            if tail_newline != -1 and tail_newline < tail_start + tail_budget * 0.2:
                tail_start = tail_newline + 1
            kept = u[:head_cut] + _units_str(MIDDLE_OMISSION_MARKER) + u[tail_start:]
            return _append_bounded_suffix(kept, len(u), max_chars)
    cut = budget
    last_newline = u.rfind("\n", 0, budget + 1)
    if last_newline > budget * 0.8:
        cut = last_newline
    return _append_bounded_suffix(u[:cut], len(u), max_chars)


def truncate_tool_result_text(text, max_chars, min_keep_chars=MIN_KEEP_CHARS):
    """Port of truncateToolResultText(text, maxChars, {minKeepChars}) with the default suffix."""
    floor = len(_suffix(1))
    min_keep = max(0, min(min_keep_chars, max(0, max_chars - floor)))  # resolveEffectiveMinKeepChars
    return _from_units(_truncate_units(_units_str(text), max_chars, min_keep))


def replay_57(text):
    """The text 5.7 keeps of a single-text-block tool result (a SKILL.md read) on later turns.

    truncateToolResultMessage with one block gives that block the whole cap
    (blockBudget = min(maxChars, max(minKeep + suffix, maxChars)) = maxChars), so this is
    truncateToolResultText at LIVE_TOOL_RESULT_MAX_CHARS.
    """
    return truncate_tool_result_text(text, LIVE_TOOL_RESULT_MAX_CHARS)


def replay_57_layout(text):
    """(mode, head_len, tail_start) of replay_57(text) in UTF-16 units: mode is "whole",
    "head-only" or "head+tail"; tail_start is the offset in ``text`` where the kept tail begins."""
    u = _units_str(text)
    if len(u) <= LIVE_TOOL_RESULT_MAX_CHARS:
        return "whole", len(u), None
    out = _units_str(replay_57(text))
    marker = _units_str(MIDDLE_OMISSION_MARKER)
    i = out.find(marker)
    if i < 0:
        return "head-only", out.rfind("[..."), None
    tail = re.sub(r"\[\.\.\. \d+ more characters truncated\]$", "", out[i + len(marker):])
    return "head+tail", i, len(u) - len(tail)
