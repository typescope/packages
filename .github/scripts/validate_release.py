#!/usr/bin/env python3
"""Validate a release PR.

Checks:
  1. PR only touches files under releases/
  2. PR touches exactly one release file
  3. A registration exists for the package
  4. If publish.github is set and GITHUB_ACTOR is known, the actor must be
     the repo owner or a public member of the repo's owning org
  5. PR appends exactly one line to the release file
  6. The appended line is valid JSON with all required fields
  7. The version format is valid (MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-modifier)
  8. The version is not already present in the release file
  9. The artifact sha512 matches the claimed value
  10. The deps field (if present) matches meta.toml from the artifact
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import urllib.error
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


class _NoRedirect(urllib.request.BaseHandler):
    """Prevent urllib from following redirects so we can inspect the status code."""
    def http_error_301(self, req, fp, code, msg, hdrs): raise urllib.error.HTTPError(req.full_url, code, msg, hdrs, fp)
    def http_error_302(self, req, fp, code, msg, hdrs): raise urllib.error.HTTPError(req.full_url, code, msg, hdrs, fp)
    def http_error_303(self, req, fp, code, msg, hdrs): raise urllib.error.HTTPError(req.full_url, code, msg, hdrs, fp)
    def http_error_307(self, req, fp, code, msg, hdrs): raise urllib.error.HTTPError(req.full_url, code, msg, hdrs, fp)


def _github_headers() -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def check_github_authorization(registry_path: Path, github_repo: str, actor: str) -> None:
    """Verify actor is the repo owner or a public member of the owning org."""
    owner = github_repo.split("/")[0]

    if actor == owner:
        return

    url = f"https://api.github.com/orgs/{owner}/members/{actor}"
    req = urllib.request.Request(url, headers=_github_headers())
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req) as resp:
            if resp.status == 204:
                return
    except urllib.error.HTTPError as e:
        if e.code in (302, 404):
            fail(
                f"'{actor}' is not authorized to publish for {registry_path}.\n"
                f"  The publish source is '{github_repo}'.\n"
                f"  The PR author must be '{owner}' or a public member of that org."
            )
        print(f"  note: could not verify GitHub authorization for {github_repo} (HTTP {e.code}), human review required")
    except Exception as e:
        print(f"  note: could not verify GitHub authorization for {github_repo} ({e}), human review required")


def main() -> None:
    base_ref = os.environ.get("BASE_REF", "origin/main")
    actor = os.environ.get("GITHUB_ACTOR")

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

    # 3. Check authorization
    try:
        with open(registry_path, "rb") as f:
            reg = tomllib.load(f)
    except Exception as e:
        fail(f"failed to parse {registry_path}: {e}")

    github_repo = reg.get("publish", {}).get("github")
    if github_repo and actor:
        check_github_authorization(registry_path, github_repo, actor)

    # 4. Check exactly one line added
    added = added_lines(release_path, base_ref)
    if len(added) != 1:
        fail(f"PR must add exactly one line to the release file, found {len(added)}")
    new_line = added[0]

    # 5. Parse and validate required fields
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

    # 6. Validate version format
    if not VERSION_RE.match(version):
        fail(f"invalid version format '{version}': must be MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-modifier")

    # 7. Check version not already present
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

    # 8. Download artifact and verify sha512
    with tempfile.TemporaryDirectory() as tmpdir:
        artifact_path = os.path.join(tmpdir, f"{package_name}-v{version}.joy")
        print(f"Downloading {url} ...")
        urllib.request.urlretrieve(url, artifact_path)

        actual_sha512 = sha512_hex(artifact_path)
        if actual_sha512 != claimed_sha512:
            fail(f"sha512 mismatch:\n  claimed: {claimed_sha512}\n  actual:  {actual_sha512}")

        # 10. Verify deps if present
        if "deps" in record:
            actual_deps = read_deps_from_artifact(artifact_path)
            if record["deps"] != actual_deps:
                fail(f"deps mismatch:\n  claimed: {record['deps']}\n  actual:  {actual_deps}")

    print(f"OK: {package_name} {version} passed all checks")


if __name__ == "__main__":
    main()
