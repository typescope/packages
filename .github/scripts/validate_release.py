#!/usr/bin/env python3
"""Validate a release PR.

Checks:
  1. PR only touches files under releases/
  2. PR touches exactly one release file
  3. A registration exists for the package
  4. PR appends exactly one line to the release file
  5. The appended line is valid JSON with all required fields
  6. The version format is valid (MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-modifier)
  7. The version is not already present in the release file
  8. The artifact sha512 matches the claimed value
  9. The deps field (if present) matches meta.toml from the artifact
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path


# Matches MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-modifier (alphanumeric modifier, no dashes).
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9]+)?$")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


def changed_files(base_ref: str) -> list[str]:
    return [f for f in git("diff", "--name-only", f"{base_ref}...HEAD").splitlines() if f.strip()]


def added_lines(path: str, base_ref: str) -> list[str]:
    diff = git("diff", f"{base_ref}...HEAD", "--", path)
    return [
        line[1:].strip()
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


def base_file_lines(path: str, base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []  # file does not exist on base branch yet
    return result.stdout.splitlines()


def sha512_hex(path: str) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_deps_from_artifact(artifact_path: str) -> dict:
    with zipfile.ZipFile(artifact_path) as zf:
        names = zf.namelist()
        meta_name = next(
            (n for n in names if n == "meta.toml" or n.endswith("/meta.toml")), None
        )
        if meta_name is None:
            fail("meta.toml not found in artifact")
        with zf.open(meta_name) as f:
            meta = tomllib.load(f)
    return meta.get("dependencies", {})


def main() -> None:
    base_ref = os.environ.get("BASE_REF", "origin/main")

    # 1. Check changed files
    changed = changed_files(base_ref)
    release_files = [f for f in changed if f.startswith("releases/") and f.endswith(".jsonl")]
    non_release = [f for f in changed if not f.startswith("releases/")]

    if non_release:
        fail(f"release PR must only touch files under releases/, found: {non_release}")
    if len(release_files) != 1:
        fail(f"release PR must touch exactly one release file, found: {release_files}")

    release_path = release_files[0]
    parts = Path(release_path).parts  # ('releases', shard, namespace, 'pkg.jsonl')
    if len(parts) != 4:
        fail(f"unexpected release file path structure: {release_path}")
    _, shard, namespace, filename = parts
    package_name = filename.removesuffix(".jsonl")

    # 2. Check registration exists
    registry_path = Path("registry") / shard / namespace / f"{package_name}.toml"
    if not registry_path.exists():
        fail(f"no registration metadata found at {registry_path}")

    # 3. Check exactly one line added
    added = added_lines(release_path, base_ref)
    if len(added) != 1:
        fail(f"PR must add exactly one line to the release file, found {len(added)}")
    new_line = added[0]

    # 4. Parse and validate required fields
    try:
        record = json.loads(new_line)
    except json.JSONDecodeError as e:
        fail(f"new release line is not valid JSON: {e}")
    for field in ("version", "url", "sha512"):
        if field not in record:
            fail(f"release line missing required field '{field}'")

    version = record["version"]
    url = record["url"]
    claimed_sha512 = record["sha512"]

    # 5. Validate version format
    if not VERSION_RE.match(version):
        fail(f"invalid version format '{version}': must be MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-modifier")

    # 6. Check version not already present
    for line in base_file_lines(release_path, base_ref):
        line = line.strip()
        if not line:
            continue
        try:
            existing = json.loads(line)
        except json.JSONDecodeError:
            continue
        if existing.get("version") == version:
            fail(f"version '{version}' is already present in {release_path}")

    # 7. Download artifact and verify sha512
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_path = os.path.join(tmpdir, f"{package_name}-v{version}.joy")
        print(f"Downloading {url} ...")
        urllib.request.urlretrieve(url, artifact_path)

        actual_sha512 = sha512_hex(artifact_path)
        if actual_sha512 != claimed_sha512:
            fail(f"sha512 mismatch:\n  claimed: {claimed_sha512}\n  actual:  {actual_sha512}")

        # 9. Verify deps if present
        if "deps" in record:
            actual_deps = read_deps_from_artifact(artifact_path)
            if record["deps"] != actual_deps:
                fail(f"deps mismatch:\n  claimed: {record['deps']}\n  actual:  {actual_deps}")

    print(f"OK: {package_name} {version} passed all checks")


if __name__ == "__main__":
    main()
