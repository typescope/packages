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
import re
import sys
import tempfile
import tomli as tomllib
import urllib.request
import urllib.error
import zipfile
from pathlib import Path


# Matches MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-modifier (alphanumeric modifier, no dashes).
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9]+)?$")

# Matches the minimum-version constraint used in deps: MAJOR.MINOR (e.g. "1.2").
DEP_VERSION_RE = re.compile(r"^\d+\.\d+$")

# Registry subtrees to skip during sync (not yet public).
IGNORE_PREFIXES: list[str] = [
    "registry/jo/jo/",
]


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

def _auth_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def github_get(url: str) -> dict | list:
    """Fetch a GitHub API URL and return parsed JSON."""
    headers = {"Accept": "application/vnd.github+json", **_auth_headers()}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def download_file(url: str, dest: Path) -> None:
    """Download url to dest."""
    req = urllib.request.Request(url, headers=_auth_headers())
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

def download_verified(url: str, dest: Path, sha512_url: str) -> str:
    """Download url to dest and verify against the publisher-provided sha512 file.

    Returns the sha512 hex digest on success, raises RuntimeError otherwise.
    """
    req = urllib.request.Request(sha512_url, headers=_auth_headers())
    with urllib.request.urlopen(req) as resp:
        expected_sha512 = resp.read().decode().split()[0].strip()

    download_file(url, dest)
    actual = sha512_hex(dest)

    if actual != expected_sha512:
        raise RuntimeError(f"sha512 mismatch: expected {expected_sha512}, got {actual}")

    return actual

def read_meta_from_artifact(path: Path) -> dict:
    """Extract fields from meta.toml inside a .joy archive. Returns {} on failure."""
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
                return tomllib.load(f)
    except Exception:
        return {}


def dep_runtime(package_name: str) -> str | None:
    """Return the runtime declared in the registry for package_name, or None if not found."""
    matches = list(Path("registry").rglob(f"{package_name}.toml"))
    if not matches:
        return None
    try:
        with open(matches[0], "rb") as f:
            reg = tomllib.load(f)
        return reg.get("runtime")
    except Exception:
        return None


def check_deps_are_pure(deps: dict) -> list[str]:
    """Return error strings for any dependency that is not a registered pure package,
    or whose version constraint is not a valid MAJOR.MINOR string."""
    errors = []
    for name, constraint in deps.items():
        if not DEP_VERSION_RE.match(str(constraint)):
            errors.append(
                f"dependency '{name}' has invalid version constraint '{constraint}'; "
                f"must be MAJOR.MINOR (e.g. '1.2')"
            )
        runtime = dep_runtime(name)
        if runtime is None:
            errors.append(f"dependency '{name}' is not registered")
        elif runtime != "pure":
            errors.append(
                f"dependency '{name}' has runtime '{runtime}'; "
                f"published packages may only depend on 'pure' packages"
            )
    return errors


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

def scan_github(name: str, repo: str, declared_runtime: str, existing: dict[str, dict],
                jsonl_path: Path, dry_run: bool) -> list[str]:
    """Scan GitHub Releases for new versions of package `name` in `repo`.

    Version is derived from the .joy asset name (<name>-v<version>.joy);
    the release tag is ignored.

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

    joy_prefix = f"{name}-v"
    joy_suffix = ".joy"
    new_count = 0

    for rel in releases:
        # Skip draft releases
        if rel.get("draft"):
            continue

        # Find all .joy assets for this package in this release
        assets: list[dict] = rel.get("assets", [])
        joy_assets = [
            a for a in assets
            if a["name"].startswith(joy_prefix) and a["name"].endswith(joy_suffix)
        ]

        for joy_asset in joy_assets:
            joy_name = joy_asset["name"]
            version = joy_name[len(joy_prefix):-len(joy_suffix)]

            # Reject malformed version strings
            if not VERSION_RE.match(version):
                errors.append(f"v{version}: asset '{joy_name}' has invalid version format, skipping")
                continue

            # Skip already-recorded versions
            if version in existing:
                continue

            src_name = f"{name}-v{version}-sources.zip"
            src_asset = next((a for a in assets if a["name"] == src_name), None)

            joy_sha512_asset = next((a for a in assets if a["name"] == joy_name + ".sha512"), None)
            src_sha512_asset = next((a for a in assets if src_asset and a["name"] == src_name + ".sha512"), None)

            if joy_sha512_asset is None:
                errors.append(f"v{version}: missing required asset '{joy_name}.sha512'")
                continue

            joy_url = joy_asset["browser_download_url"]
            joy_sha512_url = joy_sha512_asset["browser_download_url"]
            src_url = src_asset["browser_download_url"] if src_asset else None
            src_sha512_url = src_sha512_asset["browser_download_url"] if src_sha512_asset else None

            print(f"  [new]  v{version}")

            if dry_run:
                new_count += 1
                continue

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # Download and verify .joy against its .sha512
                joy_path = tmp / joy_name
                try:
                    print(f"         downloading {joy_name} ...")
                    joy_sha512 = download_verified(joy_url, joy_path, joy_sha512_url)
                except Exception as e:
                    errors.append(f"v{version}: {e}")
                    continue

                # Read meta.toml from artifact
                meta = read_meta_from_artifact(joy_path)

                # Validate runtime matches registry declaration
                artifact_runtime = meta.get("runtime", meta.get("ffi", "pure"))
                if artifact_runtime != declared_runtime:
                    errors.append(
                        f"v{version}: runtime mismatch: registry declares '{declared_runtime}', "
                        f"artifact meta.toml has runtime='{artifact_runtime}'"
                    )
                    continue

                # Validate jo version constraint
                jo_version = meta.get("jo")
                if not jo_version:
                    errors.append(f"v{version}: missing 'jo' field in meta.toml")
                    continue
                if not DEP_VERSION_RE.match(str(jo_version)):
                    errors.append(
                        f"v{version}: invalid 'jo' version '{jo_version}' in meta.toml; "
                        f"must be MAJOR.MINOR (e.g. '1.0')"
                    )
                    continue

                # Build release record
                record: dict = {
                    "version": version,
                    "url": joy_url,
                    "sha512": joy_sha512,
                    "runtime": declared_runtime,
                    "jo": jo_version,
                }

                # Extract deps from artifact and verify all are pure
                deps = meta.get("dependencies", {})
                dep_errors = check_deps_are_pure(deps)
                if dep_errors:
                    for e in dep_errors:
                        errors.append(f"v{version}: {e}")
                    continue
                if deps:
                    record["deps"] = deps

                # Optional sources archive
                if src_url and src_sha512_url:
                    src_path = tmp / src_name
                    try:
                        print(f"         downloading {src_name} ...")
                        src_sha512 = download_verified(src_url, src_path, src_sha512_url)
                        record["source_url"] = src_url
                        record["source_sha512"] = src_sha512
                    except Exception as e:
                        print(f"  [warn] v{version}: could not verify sources: {e}", file=sys.stderr)

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
    runtime = reg.get("runtime")
    publish = reg.get("publish", {})

    if not name or not namespace:
        return [f"{toml_path}: missing name or namespace"]

    if not runtime:
        return [f"{toml_path}: missing runtime field"]

    if not publish:
        return [f"{toml_path}: missing [publish] section"]

    shard = namespace_shard(namespace)
    jsonl_path = Path("releases") / shard / namespace / f"{name}.jsonl"
    existing = load_release_log(jsonl_path)

    if "github" in publish:
        return scan_github(name, publish["github"], runtime, existing, jsonl_path, dry_run)
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
    toml_files = [
        p for p in toml_files
        if p.name != ".gitkeep"
        and not any(str(p).startswith(prefix) for prefix in IGNORE_PREFIXES)
    ]

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
