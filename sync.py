#!/usr/bin/env python3
"""Registry daemon: scan registered packages and append new releases to release logs.

Usage:
    python sync.py [--dry-run]

Run from the root of the packages repository. For each registered package the
script:
  1. Reads the registration file under registry/
  2. Queries the configured publication source for new releases
  3. Downloads and sha512-verifies each new .joy artifact
  4. Appends a canonical entry to releases/.../pkg.jsonl

Set GITHUB_TOKEN in the environment to avoid GitHub API rate limits.

Exit code:
  0  all packages scanned without errors
  1  one or more packages had errors (existing releases are never modified)
"""

import hashlib
import json
import os
import sys
import tempfile
import tomllib
import urllib.request
import urllib.error
import zipfile
from pathlib import Path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> bool:
    """Return dry_run flag."""
    args = sys.argv[1:]
    if args == ["--dry-run"]:
        return True
    if args:
        print(f"usage: scan_releases.py [--dry-run]", file=sys.stderr)
        sys.exit(1)
    return False


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def github_get(url: str) -> dict | list:
    """Fetch a GitHub API URL and return parsed JSON."""
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def download_file(url: str, dest: Path) -> None:
    """Download url to dest."""
    token = os.environ.get("GITHUB_TOKEN")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        while chunk := resp.read(65536):
            f.write(chunk)


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------

def sha512_hex(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------

def read_deps_from_artifact(path: Path) -> dict[str, str]:
    """Extract dependencies from meta.toml inside a .joy archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            meta_name = next(
                (n for n in names if n == "meta.toml" or n.endswith("/meta.toml")),
                None,
            )
            if meta_name is None:
                return {}
            with zf.open(meta_name) as f:
                meta = tomllib.load(f)
        return meta.get("dependencies", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Release log
# ---------------------------------------------------------------------------

def load_release_log(jsonl_path: Path) -> dict[str, dict]:
    """Return {version: record} for all existing entries."""
    if not jsonl_path.exists():
        return {}
    records: dict[str, dict] = {}
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if "version" in rec:
                records[rec["version"]] = rec
        except json.JSONDecodeError:
            pass
    return records


def append_release(jsonl_path: Path, record: dict) -> None:
    """Append one JSON record to the release log (creates file/dirs if needed)."""
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"))
    with open(jsonl_path, "a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# GitHub Releases source
# ---------------------------------------------------------------------------

def scan_github(name: str, repo: str, existing: dict[str, dict], jsonl_path: Path,
                dry_run: bool) -> list[str]:
    """Scan GitHub Releases for new versions of package `name` in `repo`.

    Returns a list of error strings (empty on success).
    """
    errors: list[str] = []
    api_url = f"https://api.github.com/repos/{repo}/releases"
    print(f"  source: github.com/{repo}")

    try:
        releases = github_get(api_url)
    except Exception as e:
        return [f"failed to fetch GitHub releases for {repo}: {e}"]

    if not isinstance(releases, list):
        return [f"unexpected GitHub API response for {repo}"]

    new_count = 0

    for rel in releases:
        tag: str = rel.get("tag_name", "")

        # Tag must be exactly v<version>
        if not tag.startswith("v"):
            continue
        version = tag[1:]

        # Skip pre-releases and draft releases
        if rel.get("prerelease") or rel.get("draft"):
            continue

        # Skip already-recorded versions
        if version in existing:
            continue

        # Locate required .joy asset
        assets: list[dict] = rel.get("assets", [])
        joy_name = f"{name}-v{version}.joy"
        src_name = f"{name}-v{version}-sources.zip"

        joy_asset = next((a for a in assets if a["name"] == joy_name), None)
        src_asset = next((a for a in assets if a["name"] == src_name), None)

        if joy_asset is None:
            errors.append(f"  [skip] v{version}: missing required asset '{joy_name}'")
            continue

        joy_url = joy_asset["browser_download_url"]
        src_url = src_asset["browser_download_url"] if src_asset else None

        print(f"  [new]  v{version}")

        if dry_run:
            new_count += 1
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # Download and verify .joy
            joy_path = tmp / joy_name
            try:
                print(f"         downloading {joy_name} ...")
                download_file(joy_url, joy_path)
            except Exception as e:
                errors.append(f"  [error] v{version}: failed to download {joy_name}: {e}")
                continue

            joy_sha512 = sha512_hex(joy_path)

            # Build release record
            record: dict = {
                "version": version,
                "url": joy_url,
                "sha512": joy_sha512,
            }

            # Extract deps from artifact
            deps = read_deps_from_artifact(joy_path)
            if deps:
                record["deps"] = deps

            # Optional sources archive
            if src_url:
                src_path = tmp / src_name
                try:
                    print(f"         downloading {src_name} ...")
                    download_file(src_url, src_path)
                    record["source_url"] = src_url
                    record["source_sha512"] = sha512_hex(src_path)
                except Exception as e:
                    # Sources are optional; log and continue
                    print(f"  [warn] v{version}: could not download sources: {e}", file=sys.stderr)

            append_release(jsonl_path, record)
            new_count += 1
            print(f"         appended to {jsonl_path}")

    if new_count == 0 and not errors:
        print("  up-to-date")

    return errors


# ---------------------------------------------------------------------------
# Per-package scanner
# ---------------------------------------------------------------------------

def namespace_shard(namespace: str) -> str:
    return namespace[:2]


def scan_package(toml_path: Path, dry_run: bool) -> list[str]:
    """Scan one registered package. Returns list of error strings."""
    try:
        with open(toml_path, "rb") as f:
            reg = tomllib.load(f)
    except Exception as e:
        return [f"failed to parse {toml_path}: {e}"]

    name = reg.get("name")
    namespace = reg.get("namespace")
    publish = reg.get("publish", {})

    if not name or not namespace:
        return [f"{toml_path}: missing name or namespace"]

    if not publish:
        return [f"{toml_path}: missing [publish] section"]

    shard = namespace_shard(namespace)
    jsonl_path = Path("releases") / shard / namespace / f"{name}.jsonl"
    existing = load_release_log(jsonl_path)

    if "github" in publish:
        return scan_github(name, publish["github"], existing, jsonl_path, dry_run)
    else:
        known_keys = list(publish.keys())
        return [f"{toml_path}: unsupported publication source(s): {known_keys}"]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    dry_run = parse_args()

    if dry_run:
        print("=== dry run — no files will be written ===\n")

    toml_files = sorted(Path("registry").rglob("*.toml"))
    toml_files = [p for p in toml_files if p.name != ".gitkeep"]

    if not toml_files:
        print("no registered packages found under registry/")
        return

    all_errors: list[str] = []

    for toml_path in toml_files:
        print(f"package: {toml_path.stem}")
        errors = scan_package(toml_path, dry_run)
        all_errors.extend(errors)
        print()

    if all_errors:
        print("errors:", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("scan complete.")


if __name__ == "__main__":
    main()
