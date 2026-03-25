#!/usr/bin/env python3
"""Validate a registration PR.

Checks:
  1. PR only touches files under registry/
  2. Each changed .toml file has all required fields
  3. The name field matches the filename
  4. publishers is a non-empty list
"""

import subprocess
import sys
import tomllib
from pathlib import Path


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def changed_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", "HEAD~1...HEAD"],
        capture_output=True, text=True, check=True,
    )
    return [f for f in result.stdout.splitlines() if f.strip()]


REQUIRED_FIELDS = ("name", "namespace", "repo", "registered", "publishers")
REQUIRED_OWNER_FIELDS = ("name", "email")


def validate_toml(path: Path) -> None:
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

    publishers = data["publishers"]
    if not isinstance(publishers, list) or len(publishers) == 0:
        fail(f"{path}: publishers must be a non-empty list of GitHub IDs")

    owner = data.get("owner", {})
    for field in REQUIRED_OWNER_FIELDS:
        if field not in owner:
            fail(f"{path}: missing required field 'owner.{field}'")

    print(f"  ok: {path}")


def main() -> None:
    changed = changed_files()
    non_registry = [f for f in changed if not f.startswith("registry/")]
    if non_registry:
        fail(f"registration PR must only touch files under registry/, found: {non_registry}")

    toml_files = [Path(f) for f in changed if f.endswith(".toml")]
    if not toml_files:
        fail("no .toml files changed")

    for path in toml_files:
        validate_toml(path)

    print(f"OK: {len(toml_files)} registration file(s) validated")


if __name__ == "__main__":
    main()
