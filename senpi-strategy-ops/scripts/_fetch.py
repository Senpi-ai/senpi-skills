#!/usr/bin/env python3
"""Fetch a strategy package by id from the remote senpi-skills repo (stdlib only).

The agent host has the lifecycle SKILLS installed, but NOT the strategy packages — those live
in the repo under strategies/<id>/. So `deploy.py <id>` fetches the package on demand:
list the repo tree, then download every strategies/<id>/* file via raw.githubusercontent.

senpi-skills is public, so this works unauthenticated. Override repo/ref via env or pass ref=.
GITHUB_TOKEN is used if present (private repos / higher rate limit).

Unauthenticated, GitHub allows 60 API calls an hour per IP and a 304 still counts, so the tree
listing is kept in $SENPI_CATALOG_CACHE_DIR (default ~/.cache/senpi) for ten minutes, in the file the
runtime's catalog fetch (src/deploy/batch/catalog-fetch.ts) reads and writes too. When GitHub
refuses, any older listing is used instead.

  fetch_package("spider", _pkg.strategies_root())   # -> writes <root>/spider/... , returns the dir

Callers pass the ABSOLUTE durable root (_pkg.strategies_root()), never a CWD-relative path — a
package fetched into a managed skill dir is destroyed on the next skill update.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import http.client
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ.get("SENPI_SKILLS_REPO", "Senpi-ai/senpi-skills")
REF = os.environ.get("SENPI_SKILLS_REF", "main")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
LISTING_FRESH_S = 600


class FetchError(Exception):
    pass


def _get(host, path, accept, timeout):
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    headers = {"User-Agent": "senpi-strategy-ops", "Accept": accept}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body, {k.lower(): v for k, v in resp.getheaders()}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _now():
    return time.time()


def _listing_file(repo, ref):
    root = os.environ.get("SENPI_CATALOG_CACHE_DIR", "").strip() or os.path.join(Path.home(), ".cache", "senpi")
    return Path(root) / f"catalog-tree-{re.sub(r'[^A-Za-z0-9._-]', '_', f'{repo}@{ref}')}.json"


def _read_listing(path, repo, ref):
    try:
        listing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(listing, dict) or listing.get("repo") != repo or listing.get("ref") != ref
            or not isinstance(listing.get("fetchedAt"), (int, float)) or not isinstance(listing.get("paths"), list)):
        return None
    return listing


def _write_listing(path, listing):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(listing), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # the listing still serves this fetch; the next one asks GitHub again


def _refusal(status, headers, repo, ref, now):
    def num(name):
        try:
            return float(headers.get(name, ""))
        except ValueError:
            return 0.0
    reset = num("x-ratelimit-reset") or (now + num("retry-after") if num("retry-after") else 0)
    head = (f"GitHub refused the strategy catalog listing for {repo}@{ref} (HTTP {status}: this box's "
            f"address used up GitHub's 60 unauthenticated calls for the hour)")
    if not reset:
        return f"{head}. Try again in an hour."
    at = datetime.fromtimestamp(reset, timezone.utc).strftime("%H:%M")
    minutes = max(1, -(-int(reset - now) // 60))
    return f"{head}. The limit resets at {at} UTC, in {minutes} minute{'' if minutes == 1 else 's'}; try again then."


def _catalog_paths(repo, ref, timeout):
    """Every blob path in the repo at ref: the cached listing while fresh, else GitHub's tree."""
    now = _now()
    path = _listing_file(repo, ref)
    cached = _read_listing(path, repo, ref)
    if cached and now * 1000 - cached["fetchedAt"] < LISTING_FRESH_S * 1000:
        return cached["paths"]
    status, raw, headers = _get("api.github.com", f"/repos/{repo}/git/trees/{ref}?recursive=1",
                                "application/vnd.github+json", timeout)
    if status in (403, 429):
        if cached:
            return cached["paths"]
        raise FetchError(_refusal(status, headers, repo, ref, now))
    if status != 200:
        raise FetchError(f"GitHub tree API HTTP {status} for {repo}@{ref}")
    try:
        tree = json.loads(raw).get("tree", [])
    except json.JSONDecodeError as e:
        raise FetchError(f"bad tree JSON from GitHub: {e}")
    paths = [t["path"] for t in tree if t.get("type") == "blob" and isinstance(t.get("path"), str)]
    _write_listing(path, {"repo": repo, "ref": ref, "fetchedAt": int(now * 1000), "paths": paths})
    return paths


def _out_path(dest_root, tree_path):
    """Local dest for a remote tree entry (strategies/<id>/...), refusing any path that escapes
    dest_root. Defense-in-depth: git won't emit `..` in tree paths, but the repo/ref this fetches
    from are env-overridable (SENPI_SKILLS_REPO/_REF)."""
    out = Path(dest_root) / tree_path[len("strategies/"):]
    try:  # relative_to+ValueError, not is_relative_to: hosts are documented Python 3.8+
        out.resolve().relative_to(Path(dest_root).resolve())
    except ValueError:
        raise FetchError(f"remote tree entry {tree_path!r} escapes the dest root — refusing")
    return out


def fetch_package(strategy_id, dest_root, ref=None, repo=None, timeout=30):
    """Download strategies/<strategy_id>/ from the remote repo into <dest_root>/<strategy_id>.

    Returns the local package Path. Raises FetchError on any network / not-found failure.
    """
    ref = ref or REF
    repo = repo or REPO
    # 1. one recursive tree listing (cached) → all blob paths under strategies/<id>/
    paths = _catalog_paths(repo, ref, timeout)
    prefix = f"strategies/{strategy_id}/"
    files = [p for p in paths if p.startswith(prefix)]
    src_prefix, resolved_from = prefix, None
    if not files and "-" in strategy_id:
        # A DEPLOYED package is normally the user's fork, named "<username>-<template>". The fork
        # exists only on that user's box; the repo only ever carries the template. So once the local
        # copy is gone — closed, or deleted by hand — "reinstall <username>-athena" has to resolve
        # to strategies/athena/, or the id reads as a package that simply does not exist.
        #
        # Match the SUFFIX against the templates the repo actually carries, rather than splitting on
        # the first "-": a Senpi username may contain hyphens ("john-doe-athena") and guessing the
        # split point gets those wrong. Longest match first so a template cannot be shadowed by a
        # shorter one that happens to be a suffix of it (athena-x vs athena).
        templates = {q.split("/", 2)[1] for q in paths
                     if q.startswith("strategies/") and q.count("/") >= 2}
        base = next((c for c in sorted(templates, key=len, reverse=True)
                     if strategy_id.endswith(f"-{c}")), None)
        if base:
            src_prefix, resolved_from = f"strategies/{base}/", base
            files = [p for p in paths if p.startswith(src_prefix)]
    if not files:
        raise FetchError(
            f"strategy {strategy_id!r} not found under strategies/ on {repo}@{ref}. "
            f"A deployed package is usually a FORK named <username>-<template> and only the "
            f"template is in the repo — try the template id alone. Do not look for a GitHub "
            f"user or repo by that name: the prefix is a Senpi username.")
    # 2. download each file via raw.
    # ALWAYS the id the CALLER asked for. deploy.py and close.py both rebuild this path from the id
    # they passed, so writing the template's own name instead means the package they just downloaded
    # is invisible to them — the download succeeds and the caller reports "no strategy.yaml", which
    # is both false and points away from the cause.
    dest = Path(dest_root) / strategy_id
    for path in files:
        status, content, _ = _get("raw.githubusercontent.com", f"/{repo}/{ref}/{path}", "*/*", timeout)
        if status != 200:
            raise FetchError(f"raw fetch HTTP {status} for {path}")
        # Remap onto the requested id. The prefix is ours and only the relative tail comes from the
        # tree, so _out_path's traversal guard still governs every byte of the path it sees.
        out = _out_path(dest_root, prefix + path[len(src_prefix):])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    if resolved_from:
        # Say it. The fork is the user's Make It Yours copy; handing back the pristine template
        # under the fork's name without a word means any tuning they did is silently not there.
        print(f"[senpi-strategy-ops] {strategy_id!r} is not in the repo — fetched the "
              f"{resolved_from!r} template under that name. Any local edits the fork had are "
              f"NOT in this copy.", file=sys.stderr)
    return dest
