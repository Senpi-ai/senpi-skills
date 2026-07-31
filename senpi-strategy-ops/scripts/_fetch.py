#!/usr/bin/env python3
"""Fetch a strategy package by id from the remote senpi-skills repo (stdlib only).

The agent host has the lifecycle SKILLS installed, but NOT the strategy packages — those live
in the repo under strategies/<id>/. So `deploy.py <id>` fetches the package on demand:
list the repo tree, then download every strategies/<id>/* file via raw.githubusercontent.

senpi-skills is public, so this works unauthenticated. Override repo/ref via env or pass ref=.
GITHUB_TOKEN is used if present (private repos / higher rate limit).

  fetch_package("spider", _pkg.strategies_root())   # -> writes <root>/spider/... , returns the dir

Callers pass the ABSOLUTE durable root (_pkg.strategies_root()), never a CWD-relative path — a
package fetched into a managed skill dir is destroyed on the next skill update.
"""
# Copyright 2026 Senpi (https://senpi.ai) — Apache-2.0
import http.client
import json
import os
from pathlib import Path

REPO = os.environ.get("SENPI_SKILLS_REPO", "Senpi-ai/senpi-skills")
REF = os.environ.get("SENPI_SKILLS_REF", "main")
TOKEN = os.environ.get("GITHUB_TOKEN", "")


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
        return resp.status, body
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


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
    # 1. one recursive tree listing → all blob paths under strategies/<id>/
    status, raw = _get("api.github.com", f"/repos/{repo}/git/trees/{ref}?recursive=1",
                       "application/vnd.github+json", timeout)
    if status != 200:
        raise FetchError(f"GitHub tree API HTTP {status} for {repo}@{ref} "
                         f"(rate limit? set GITHUB_TOKEN)")
    try:
        tree = json.loads(raw).get("tree", [])
    except json.JSONDecodeError as e:
        raise FetchError(f"bad tree JSON from GitHub: {e}")
    prefix = f"strategies/{strategy_id}/"
    files = [t["path"] for t in tree if t.get("type") == "blob" and t.get("path", "").startswith(prefix)]
    if not files:
        raise FetchError(f"strategy {strategy_id!r} not found under strategies/ on {repo}@{ref}")
    # 2. download each file via raw
    dest = Path(dest_root) / strategy_id
    for path in files:
        status, content = _get("raw.githubusercontent.com", f"/{repo}/{ref}/{path}", "*/*", timeout)
        if status != 200:
            raise FetchError(f"raw fetch HTTP {status} for {path}")
        out = _out_path(dest_root, path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
    return dest
