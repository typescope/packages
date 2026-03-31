#!/usr/bin/env python3
"""Validate a registration PR.

Checks:
  1. PR only touches files under registry/
  2. Each changed .toml file has all required fields
  3. The name field matches the filename
  4. If the namespace is already used by another registration, the new
     registration's owner.name and owner.email must match the existing owner
  5. Immutable fields (name, namespace, registered) are not changed in updates
  6. [publish] section is present and uses github as its source
  7. GITHUB_ACTOR must be known, and the actor must be the repo owner or a public
     member of the repo's owning org
"""

import os
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


def changed_files(base_ref: str) -> list[str]:
    return [f for f in git("diff", "--name-only", f"{base_ref}...HEAD").splitlines() if f.strip()]


REQUIRED_FIELDS = ("name", "namespace", "repo", "registered", "publish")
REQUIRED_OWNER_FIELDS = ("name", "email")
IMMUTABLE_FIELDS = ("name", "namespace", "registered")


def namespace_shard(namespace: str) -> str:
    return namespace[:2]


def base_registration(path: Path, base_ref: str) -> dict | None:
    """Return the parsed base-branch content of path, or None if it is a new file."""
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    try:
        return tomllib.loads(result.stdout.decode())
    except Exception:
        return None


def existing_registrations_for_namespace(namespace: str, base_ref: str) -> list[dict]:
    """Return parsed data for all existing (base-branch) registrations under a namespace."""
    shard = namespace_shard(namespace)
    ns_dir = Path("registry") / shard / namespace
    registrations: list[dict] = []

    if not ns_dir.is_dir():
        return registrations

    for toml_path in sorted(ns_dir.glob("*.toml")):
        result = subprocess.run(
            ["git", "show", f"{base_ref}:{toml_path}"],
            capture_output=True,
        )
        if result.returncode != 0:
            continue  # file is new in this PR, not on base branch
        try:
            registrations.append(tomllib.loads(result.stdout.decode()))
        except Exception:
            pass

    return registrations


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


def check_repo_exists(path: Path, github_repo: str) -> None:
    """Verify the GitHub repo exists and is accessible.

    Exits with an error if the repo does not exist.
    Prints a note and returns if the check is inconclusive (API error).
    """
    url = f"https://api.github.com/repos/{github_repo}"
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                return  # repo exists
    except urllib.error.HTTPError as e:
        if e.code == 404:
            fail(f"{path}: GitHub repository '{github_repo}' does not exist or is not accessible")
        print(f"  note: could not verify repo existence for {github_repo} (HTTP {e.code}), human review required")
    except Exception as e:
        print(f"  note: could not verify repo existence for {github_repo} ({e}), human review required")


def check_github_authorization(path: Path, github_repo: str, actor: str) -> None:
    """Verify actor is the repo owner or a public member of the repo's owning org.

    Exits with an error if authorization cannot be confirmed.
    Prints a note and returns if the check is inconclusive (API error).
    """
    owner = github_repo.split("/")[0]

    # Personal repo: actor must be the owner.
    if actor == owner:
        return

    # Org repo: check if actor is a public org member.
    url = f"https://api.github.com/orgs/{owner}/members/{actor}"
    req = urllib.request.Request(url, headers=_github_headers())
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req) as resp:
            if resp.status == 204:
                return  # confirmed public org member
    except urllib.error.HTTPError as e:
        if e.code in (302, 404):
            fail(
                f"{path}: '{actor}' is not authorized to modify this registration.\n"
                f"  The publish source is '{github_repo}'.\n"
                f"  The PR author must be '{owner}' or a public member of that org."
            )
        # Other HTTP errors (rate limit, server error): inconclusive, skip check.
        print(f"  note: could not verify GitHub authorization for {github_repo} (HTTP {e.code}), human review required")
    except Exception as e:
        print(f"  note: could not verify GitHub authorization for {github_repo} ({e}), human review required")


def validate_toml(path: Path, base_ref: str, actor: str | None) -> None:
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        fail(f"{path}: failed to parse TOML: {e}")

    for field in REQUIRED_FIELDS:
        if field not in data:
            fail(f"{path}: missing required field '{field}'")

    name = data["name"]
    expected_name = path.stem
    if name != expected_name:
        fail(f"{path}: name '{name}' does not match filename '{expected_name}'")

    base = base_registration(path, base_ref)
    if base is not None:
        for field in IMMUTABLE_FIELDS:
            old_val = base.get(field)
            new_val = data.get(field)
            if old_val != new_val:
                fail(f"{path}: '{field}' is immutable and cannot be changed (was '{old_val}', got '{new_val}')")

    owner = data.get("owner", {})
    for field in REQUIRED_OWNER_FIELDS:
        if field not in owner:
            fail(f"{path}: missing required field 'owner.{field}'")

    namespace = data["namespace"]
    existing = existing_registrations_for_namespace(namespace, base_ref)
    if existing:
        existing_owner = existing[0].get("owner", {})
        new_owner = data.get("owner", {})
        if new_owner.get("name") != existing_owner.get("name"):
            fail(
                f"{path}: owner.name must match the existing owner of namespace '{namespace}': "
                f"expected '{existing_owner.get('name')}', got '{new_owner.get('name')}'"
            )
        if new_owner.get("email") != existing_owner.get("email"):
            fail(
                f"{path}: owner.email must match the existing owner of namespace '{namespace}': "
                f"expected '{existing_owner.get('email')}', got '{new_owner.get('email')}'"
            )

    publish = data.get("publish", {})
    if "github" not in publish:
        fail(f"{path}: [publish] must have a 'github' key (only GitHub is supported as a publish source)")
    unsupported = [k for k in publish if k != "github"]
    if unsupported:
        fail(f"{path}: unsupported publish source(s): {unsupported} (only 'github' is supported)")

    # For authorization, always use the base-branch publish.github so an attacker
    # cannot change publish.github to a repo they own and pass the check.
    github_repo = base.get("publish", {}).get("github") if base is not None else publish["github"]

    # For new registrations, verify the declared repo actually exists.
    if base is None:
        check_repo_exists(path, github_repo)

    if not actor:
        fail(f"{path}: GITHUB_ACTOR is not set; cannot verify authorization")
    check_github_authorization(path, github_repo, actor)

    print(f"  ok: {path}")


def main() -> None:
    base_ref = os.environ.get("BASE_REF", "origin/main")
    actor = os.environ.get("GITHUB_ACTOR")

    changed = changed_files(base_ref)
    non_registry = [f for f in changed if not f.startswith("registry/")]
    if non_registry:
        fail(f"registration PR must only touch files under registry/, found: {non_registry}")

    toml_files = [Path(f) for f in changed if f.endswith(".toml")]
    if not toml_files:
        fail("no .toml files changed")

    for path in toml_files:
        validate_toml(path, base_ref, actor)

    print(f"OK: {len(toml_files)} registration file(s) validated")


if __name__ == "__main__":
    main()
