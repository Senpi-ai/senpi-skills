#!/usr/bin/env python3
"""Tiny stdlib-only YAML safe-loader — the subset the strategy.yaml + runtime.yaml packages use.

Why: the deploy/close scripts run on agent hosts that may not have PyYAML (or pip). `_pkg` uses real
PyYAML when importable and falls back to this. It is NOT a general YAML parser — it covers exactly what
our packages use: block maps + sequences, nested blocks, flow `[...]`/`{...}`, scalars (str/int/float/
bool/null), single/double quotes, `#` comments, and `>`/`|` block scalars (captured loosely as text —
we don't interpret descriptions). Validated for byte-equality against PyYAML on the example packages.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import re

_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


class YAMLError(Exception):
    pass


# ---------- scalars + comments ----------

def _strip_comment(s):
    """Remove a trailing ` # comment` at depth 0 outside quotes (YAML: # starts a comment at col 0 or
    after whitespace)."""
    depth = 0
    q = None
    for i, c in enumerate(s):
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == "#" and depth == 0 and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _scalar(tok):
    tok = tok.strip()
    if tok == "" or tok == "~" or tok.lower() == "null":
        return None
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    low = tok.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT_RE.match(tok):
        return int(tok)
    if _FLOAT_RE.match(tok):
        return float(tok)
    return tok


# ---------- flow [...] / {...} ----------

def _split_top(s):
    """Split a flow body on top-level commas (respecting nesting + quotes)."""
    out, depth, q, start = [], 0, None, 0
    for i, c in enumerate(s):
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(s[start:i])
            start = i + 1
    tail = s[start:]
    if tail.strip() or out:
        out.append(tail)
    return out


def _split_kv(item):
    """Split a flow-map entry on the first top-level ':'."""
    depth, q = 0, None
    for i, c in enumerate(item):
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == ":" and depth == 0:
            return item[:i], item[i + 1:]
    return item, None


def _parse_flow(s):
    s = s.strip()
    if s.startswith("["):
        if not s.endswith("]"):
            raise YAMLError(f"unterminated flow sequence: {s!r}")
        inner = s[1:-1].strip()
        return [_parse_flow(x) for x in _split_top(inner)] if inner else []
    if s.startswith("{"):
        if not s.endswith("}"):
            raise YAMLError(f"unterminated flow mapping: {s!r}")
        inner = s[1:-1].strip()
        d = {}
        for item in (_split_top(inner) if inner else []):
            if not item.strip():
                continue
            k, v = _split_kv(item)
            if v is None:
                raise YAMLError(f"bad flow mapping entry: {item!r}")
            d[str(_scalar(k))] = _parse_flow(v)
        return d
    return _scalar(s)


def _value(s):
    """A value that may be a flow collection or a plain scalar."""
    s = _strip_comment(s).strip()
    if s[:1] in "[{":
        return _parse_flow(s)
    return _scalar(s)


# ---------- block parser ----------

def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _key_split(content):
    """Split a block-mapping line into (key, rest) on the first ': ' / trailing ':' at depth 0 outside
    quotes/flow. Returns (None, None) if it is not a mapping line."""
    depth, q = 0, None
    for i, c in enumerate(content):
        if q:
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == ":" and depth == 0 and (i + 1 == len(content) or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1:]
    return None, None


class _P:
    def __init__(self, text):
        self.lines = text.split("\n")
        self.i = 0

    def _skip(self):
        while self.i < len(self.lines):
            s = self.lines[self.i].strip()
            if s == "" or s.startswith("#"):
                self.i += 1
            else:
                return

    def parse_node(self, indent):
        self._skip()
        if self.i >= len(self.lines):
            return None
        ci = _indent(self.lines[self.i])
        if ci < indent:
            return None
        content = self.lines[self.i].strip()
        if content[:1] in "[{":
            self.i += 1
            return _value(content)
        if content == "-" or content.startswith("- "):
            return self.parse_seq(ci)
        key, _rest = _key_split(content)
        if key is not None:
            return self.parse_map(ci)
        self.i += 1
        return _value(content)

    def parse_map(self, indent):
        d = {}
        while True:
            self._skip()
            if self.i >= len(self.lines):
                break
            ci = _indent(self.lines[self.i])
            if ci != indent:
                break
            content = self.lines[self.i].strip()
            key, rest = _key_split(content)
            if key is None:
                break
            self.i += 1
            rest = (rest or "").strip()
            stripped = _strip_comment(rest).strip()
            if stripped in (">", "|", ">-", "|-", ">+", "|+"):
                d[str(_scalar(key))] = self._block_scalar(indent)
            elif rest == "" or _strip_comment(rest).strip() == "":
                d[str(_scalar(key))] = self.parse_node(indent + 1)
            else:
                d[str(_scalar(key))] = _value(rest)
        return d

    def parse_seq(self, indent):
        items = []
        while True:
            self._skip()
            if self.i >= len(self.lines):
                break
            line = self.lines[self.i]
            if _indent(line) != indent:
                break
            stripped = line.strip()
            if stripped != "-" and not stripped.startswith("- "):
                break
            # rewrite the leading '-' to a space so the item content sits at indent+2 and reparse
            self.lines[self.i] = line[:indent] + " " + line[indent + 1:]
            if stripped == "-":
                self.i += 1
                items.append(self.parse_node(indent + 1))
            else:
                items.append(self.parse_node(indent + 2))
        return items

    def _block_scalar(self, key_indent):
        """Capture a `>`/`|` block as text (folded loosely). We never interpret these, just consume."""
        out = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == "":
                out.append("")
                self.i += 1
                continue
            if _indent(line) <= key_indent:
                break
            out.append(line.strip())
            self.i += 1
        return " ".join(x for x in out if x).strip()


def safe_load(text):
    if text is None:
        return None
    p = _P(text)
    p._skip()
    if p.i >= len(p.lines):
        return None
    return p.parse_node(_indent(p.lines[p.i]))
