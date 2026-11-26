#!/usr/bin/env python3
"""A template deploys under the user's name — `fork()` is how.

It copies a catalog package to `<strategies root>/<owner>-<template>/` (or `<their-words>/`) and
rewrites the four things that carry identity: the manifest `id`, its `catalog.name`, each runtime's
`name`/`group` linkage and the first line of its `description` (the mandate senpi-portfolio reads
back). `forked_from` records the lineage, and `forked_from.parent` the node the copy's deploys count
under. Everything else stays byte-identical — the edits are textual, never a YAML re-dump, so comments
and layout survive. Levers are moved afterwards with the normal edit path, never here.

Two names, on purpose. The backend strategy name is derived by the deploy verb from the package id
and allows only `[A-Za-z0-9_-]` (and the backend lowercases it), so the id is `ignas-phalanx`. The
display name — `Ignas's Phalanx` — lives in `catalog.name` and the runtime description, and is what
the agent says. Both are printed by every verb that makes or deploys a fork.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import _pkg

MAX_ID = 40        # the deploy verb's sanitizeStrategyName cap (runtime src/deploy/package.ts)
MAX_OWNER = 20
MIN_ID = 3         # the backend's floor for a strategy name
# A simulation installs as `<id>-<instance>-simulation` under MAX_ID, and the runtime refuses an
# `<id>-<instance>` longer than this rather than cutting it (src/deploy/package.ts).
SIMULATION_BASE_MAX = MAX_ID - len("-simulation")
SKIP_NAMES = {".deploy-state.json", ".senpi-proof.json", "__pycache__", ".DS_Store"}


class ForkError(ValueError):
    """A fork that must not be made — the message is the user-facing reason."""


def slug(text, cap=MAX_ID):
    """Lowercase `[a-z0-9-]`, words joined by '-', capped — the shape the deploy verb accepts unchanged."""
    s = re.sub(r"[^a-z0-9-]", "", re.sub(r"[\s_]+", "-", str(text or "").strip().lower()))
    return re.sub(r"-{2,}", "-", s).strip("-")[:cap].strip("-")


def _fit(fid, budget, seed=None):
    """`fid` when it fits `budget`; else its head plus a hash of the uncut id, so two ids cut to one head stay apart."""
    if len(fid) <= budget:
        return fid
    h = hashlib.sha256((seed or fid).encode()).hexdigest()[:6]
    return f"{fid[:budget - len(h) - 1].rstrip('-')}-{h}"


def short_title(catalog_name, pkg_id):
    """`Phalanx — Proven-Cohort Rotation` → `Phalanx`: the name before the tagline separator."""
    t = re.split(r"\s[—–-]\s|:\s", str(catalog_name or pkg_id or "").strip(), 1)[0].strip()
    return t or str(pkg_id)


def names_for(pkg, owner=None, name=None):
    """`(id, display)` for a fork of `pkg`: `--name` (their words) wins; else `<owner>-<template>`."""
    for given in (name, owner):                     # a user ID is never a name, whichever flag it arrives in
        if re.fullmatch(r"m\d+(-[0-9a-z]+)?", str(given or "").strip(), re.IGNORECASE):
            raise ForkError(f"{given!r} is the user's Senpi ID, not a name: leave --owner off and their username is "
                            f"read for you, or ask what to call it and pass --name <their words>")
    budget = SIMULATION_BASE_MAX - 1 - max((len(i.name) for i in pkg.instances), default=0)
    if name and str(name).strip():
        fid, display = _fit(slug(name), budget), str(name).strip()
    elif owner and str(owner).strip():
        o = slug(owner, MAX_OWNER)
        if not o:
            raise ForkError(f"owner {owner!r} leaves nothing usable in a strategy id — pass --name <their words> instead")
        fid = f"{o}-{pkg.id}"[:MAX_ID].strip("-")
        room = budget - len(pkg.id) - 1
        if len(fid) > budget and room >= MIN_ID:     # keep the whole template, cut the username
            fid = f"{slug(owner, room)}-{pkg.id}"
        fid = _fit(fid, budget, seed=f"{slug(owner, 200)}-{pkg.id}")
        display = f"{str(owner).strip()}'s {short_title((pkg.catalog or {}).get('name'), pkg.id)}"
    else:
        raise ForkError("a template deploys under the user's name, and their Senpi username could not be read: "
                        "ask what to call it and pass --name <their words>")
    if len(fid) < MIN_ID:
        raise ForkError(f"{fid!r} is shorter than the {MIN_ID} characters a strategy name needs — pass --name <their words>")
    return fid, display


def _set_top_key(text, key, value):
    """Replace the first column-0 `key:` line, or insert one before the first non-comment line."""
    pat = re.compile(rf"(?m)^{re.escape(key)}:[ \t]*.*$")
    if pat.search(text):
        return pat.sub(f"{key}: {value}", text, count=1)
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if l.strip() and not l.lstrip().startswith("#")), 0)
    lines.insert(at, f"{key}: {value}")
    return "\n".join(lines)


def _set_catalog_name(text, display):
    """`catalog.name` — the first indented `name:` inside the column-0 `catalog:` block."""
    lines = text.split("\n")
    quoted = '"' + display.replace('"', '\\"') + '"'
    start = next((i for i, l in enumerate(lines) if re.match(r"^catalog:\s*(#.*)?$", l)), None)
    if start is None:
        raise ForkError("strategy.yaml has no `catalog:` block to name the fork in")
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.strip() and not l.startswith((" ", "\t")):
            break                                   # the block ended
        m = re.match(r"^(\s+)name:\s*.*$", l)
        if m:
            lines[i] = f"{m.group(1)}name: {quoted}"
            return "\n".join(lines)
    lines.insert(start + 1, f"  name: {quoted}")
    return "\n".join(lines)


def _add_forked_from(text, template_id, version, parent):
    block = f'forked_from:\n  id: {template_id}\n  version: "{version}"\n  parent: {json.dumps(parent, ensure_ascii=False)}'
    lines = text.split("\n")
    at = next(i for i, l in enumerate(lines) if re.match(r"^version:", l))     # validate requires `version`
    lines.insert(at + 1, block)
    return "\n".join(lines)


def _set_parent(text, ref, ff):
    """Write `parent: <ref>` into the column-0 `forked_from:` block, replacing a `parent:` already there.
    A one-line flow mapping (`forked_from: {id: x, version: y}`) is rewritten as a block from `ff`, its
    parsed value."""
    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^forked_from:\s*(#.*)?$", l)), None)
    if start is None:
        flow = next((i for i, l in enumerate(lines) if re.match(r"^forked_from:\s*\{.*\}\s*(#.*)?$", l)), None)
        if flow is None:
            raise ForkError("strategy.yaml's `forked_from` is neither a block nor a one-line mapping, so the "
                            "copy's parent cannot be written")
        items = {**ff, "parent": ref}
        lines[flow:flow + 1] = ["forked_from:"] + [
            f"  {k}: {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in items.items()]
        return "\n".join(lines)
    block = []
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if l.strip() and not l.startswith((" ", "\t")) and not l.startswith("#"):
            break                                   # the block ended
        if l.strip() and not l.lstrip().startswith("#"):
            block.append(i)
    indent = re.match(r"^(\s*)", lines[block[0]]).group(1) if block else "  "
    value = f"{indent}parent: {json.dumps(ref, ensure_ascii=False)}"
    at = next((i for i in block if re.match(rf"^{indent}parent:(\s|$)", lines[i])), None)
    if at is None:
        lines.insert(start + 1, value)
        return "\n".join(lines)
    end = at + 1                                    # the old value's continuation lines go with it
    for j in range(at + 1, len(lines)):
        l = lines[j]
        if l.strip() and len(l) - len(l.lstrip()) <= len(indent):
            break
        if l.strip():
            end = j + 1
    lines[at:end] = [value]
    return "\n".join(lines)


def _parent_ref(pkg, parent_ff, fork_of, authored):
    """The ref a copy of `pkg` records in `forked_from.parent`, or None when nothing is written: the source's
    own parent is kept, or a fork with neither a parent nor a template id leaves the copy without one."""
    if fork_of is not None:
        return fork_of
    if parent_ff is not None:
        if str(parent_ff.get("parent") or "").strip():
            return None
        tid = str(parent_ff.get("id") or "").strip()
        return f"template:{tid}" if tid else None
    return f"custom:{pkg.id}" if authored else f"template:{pkg.id}"


def _prefix_description(text, prefix):
    """Put `prefix` first in the runtime `description` — block scalar, quoted line or absent."""
    lines = text.split("\n")
    at = next((i for i, l in enumerate(lines) if re.match(r"^description:", l)), None)
    if at is None:
        anchor = next((i for i, l in enumerate(lines) if re.match(r"^group:", l)), 0)
        lines[anchor + 1:anchor + 1] = ["description: >", f"  {prefix}"]
        return "\n".join(lines)
    head = lines[at]
    m = re.match(r"^description:\s*([>|][-+]?)\s*(#.*)?$", head)
    if m:                                            # block scalar: a new first line at the block's indent
        indent = "  "
        for l in lines[at + 1:]:
            if l.strip():
                indent = re.match(r"^(\s*)", l).group(1) or "  "
                break
        lines.insert(at + 1, f"{indent}{prefix}")
        return "\n".join(lines)
    raw = head.split(":", 1)[1].strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].replace('\\"', '"')
    lines[at:at + 1] = ["description: >", f"  {prefix}"] + ([f"  {raw}"] if raw else [])
    return "\n".join(lines)


def _ignore(_dir, names):
    return [n for n in names if n in SKIP_NAMES or n.startswith((".senpi-fetch-", ".senpi-fork-")) or ".bak-" in n]


def _existing(pkg, dest, parent_ff, fork_of, info, log):
    """`dest` already exists: reuse it when `pkg` is not itself a fork, `dest` is a fork of `pkg` and, with
    `fork_of`, `dest` records `fork_of` as its parent; otherwise refuse. Nothing is written."""
    if parent_ff is not None:
        raise ForkError(f"{dest} exists — deploy it by directory if it is this copy, or pass --name <another name>")
    try:
        have = _pkg.load(dest)
    except _pkg.BadPackage as e:
        raise ForkError(f"{dest} exists but is not a package ({e}) — move it aside or pass --name <another name>")
    ff = (have.manifest or {}).get("forked_from") or {}
    if ff.get("id") != pkg.id:
        raise ForkError(f"{dest} exists and is not a fork of {pkg.id} — pass --name <another name>")
    if fork_of is not None and str(ff.get("parent") or "").strip() != fork_of:
        raise ForkError(f"{dest} exists and was not copied from {fork_of} (its parent is "
                        f"{ff.get('parent') or 'not recorded'}) — pass --name <another name>")
    info["reused"] = True
    info["display"] = (have.catalog or {}).get("name") or info["display"]
    log(f"  fork {info['id']} already exists ({info['display']}, from {ff.get('id')} {ff.get('version')}) — using it")
    return dest, info


def fork(pkg, root, owner=None, name=None, fork_of=None, authored=False, log=lambda m: None):
    """Make (or reuse) the user's fork of `pkg` under `root`. Returns `(dir, info)`.

    Reuse is by lineage: an existing directory is returned untouched when it is a fork of this same
    template (a live fork keeps its deploy state; a fork is a copy, so a newer template is not grafted
    onto it). Any other occupant of the name is refused rather than overwritten.

    A package that is already a fork is copied, edits and all, only under `name`: that is how a variation
    of a user's own strategy starts. The copy keeps the template lineage and is never reused.

    The copy records `forked_from.parent`: `fork_of` (the strategy id it starts from) when given, else the
    source's own parent, else `template:<forked_from.id>` for a fork, `custom:<id>` for an `authored` package
    and `template:<id>` for a catalog one. `fork_of` needs `name`; a reused directory is left as it is, and
    with `fork_of` it is reused only when its parent is `fork_of`."""
    if fork_of is not None:
        fork_of = str(fork_of).strip()
        if not fork_of:
            raise ForkError("--fork-of needs the id of the strategy this copy starts from")
        if not (name and str(name).strip()):
            raise ForkError("a copy started from a running strategy needs a name of its own: ask what to call it "
                            "and pass --name <their words>")
    parent_ff = (pkg.manifest or {}).get("forked_from")
    if parent_ff is not None and not (name and str(name).strip()):
        ff = parent_ff or {}
        raise ForkError(f"{pkg.id} is already a fork (of {ff.get('id')} {ff.get('version')}) — deploy it by "
                        f"directory, or copy it with its edits under --name <their words>")
    fid, display = names_for(pkg, owner, name)
    root = Path(root)
    dest = root / fid
    lineage = dict(id=str(parent_ff.get("id")), version=str(parent_ff.get("version"))) if parent_ff else \
        dict(id=pkg.id, version=str(pkg.version))
    info = dict(id=fid, display=display, forked_from=lineage,
                runtimes=[f"{fid}-{i.name}" for i in pkg.instances], dir=str(dest), reused=False)
    if dest.exists():
        return _existing(pkg, dest, parent_ff, fork_of, info, log)
    root.mkdir(parents=True, exist_ok=True)
    # The copy is built and validated in a staging directory inside `root`, then renamed to `dest` in
    # one step; a run stopped before the rename leaves only the staging directory.
    staging = Path(tempfile.mkdtemp(prefix=f".senpi-fork-{fid}-", dir=root))
    built = staging / fid
    try:
        shutil.copytree(pkg.dir, built, ignore=_ignore)
        man = built / "strategy.yaml"
        text = man.read_text()
        text = _set_top_key(text, "id", fid)
        text = _set_catalog_name(text, display)
        ref = _parent_ref(pkg, parent_ff, fork_of, authored)
        if parent_ff is None:
            text = _add_forked_from(text, pkg.id, pkg.version, ref)
        elif ref is not None:
            text = _set_parent(text, ref, parent_ff)
        man.write_text(text)
        title = short_title((pkg.catalog or {}).get("name"), pkg.id)
        origin = f"copied from {(pkg.catalog or {}).get('name') or pkg.id}" if parent_ff is not None else \
            f"forked from {title} {pkg.version}"
        for inst in pkg.instances:
            rp = built / inst.runtime_rel
            t = rp.read_text()
            t = _set_top_key(t, "name", f"{fid}-{inst.name}")
            t = _set_top_key(t, "group", fid)
            t = _prefix_description(t, f"{display} — {origin}.")
            rp.write_text(t)
        errs = _pkg.validate(_pkg.load(built))
        if errs:
            raise ForkError("the fork did not validate — nothing kept: " + "; ".join(errs))
        try:
            os.rename(built, dest)
        except OSError:
            if not dest.exists():
                raise
            log(f"  {dest} was made by another run while this one copied")
            return _existing(pkg, dest, parent_ff, fork_of, info, log)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"  {'copied' if parent_ff is not None else 'forked'} {pkg.id} {pkg.version} → {dest} ({display})")
    return dest, info
