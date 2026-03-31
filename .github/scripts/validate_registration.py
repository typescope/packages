#!/usr/bin/env python3
"""Validate a registration PR.

Checks:
  1. PR only touches files under registry/
  2. Each changed .toml file has all required fields
  3. The name field matches the filename
  4. If the namespace is already used by another registration, the new
     registration's owner.name and owner.email must match the existing owner
"""

import os
import subprocess
import sys
import tomllib
from pathlib import Path


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout


def changed_files(base_ref: str) -> list[str]:
    return [f for f in git("diff", "--name-only", f"{base_ref}...HEAD").splitlines() if f.strip()]


REQUIRED_FIELDS = ("name", "namespace", "repo", "registered")
REQUIRED_OWNER_FIELDS = ("name", "email")


def namespace_shard(namespace: str) -> str:
    return namespace[:2]


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


def validate_toml(path: Path, base_ref: str) -> None:
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

    print(f"  ok: {path}")


def main() -> None:
    base_ref = os.environ.get("BASE_REF", "origin/main")

    changed = changed_files(base_ref)
    non_registry = [f for f in changed if not f.startswith("registry/")]
    if non_registry:
        fail(f"registration PR must only touch files under registry/, found: {non_registry}")

    toml_files = [Path(f) for f in changed if f.endswith(".toml")]
    if not toml_files:
        fail("no .toml files changed")

    for path in toml_files:
        validate_toml(path, base_ref)

    print(f"OK: {len(toml_files)} registration file(s) validated")


if __name__ == "__main__":
    main()
