#!/usr/bin/env python3
"""Build the flat registry for Cloudflare Pages.

Flattens releases/<shard>/<namespace>/<package>.jsonl -> dist/<package>.jsonl
Flattens registry/<shard>/<namespace>/<package>.toml  -> dist/<package>.toml
"""

import shutil
from pathlib import Path

dist = Path("dist")
if dist.exists():
    shutil.rmtree(dist)
dist.mkdir()

jsonl_count = 0
for jsonl in sorted(Path("releases").rglob("*.jsonl")):
    dest = dist / jsonl.name
    if dest.exists():
        print(f"CONFLICT: {jsonl.name} appears more than once under releases/")
        raise SystemExit(1)
    shutil.copy(jsonl, dest)
    print(f"  {jsonl} -> dist/{jsonl.name}")
    jsonl_count += 1

toml_count = 0
for toml in sorted(Path("registry").rglob("*.toml")):
    dest = dist / toml.name
    if dest.exists():
        print(f"CONFLICT: {toml.name} appears more than once under registry/")
        raise SystemExit(1)
    shutil.copy(toml, dest)
    print(f"  {toml} -> dist/{toml.name}")
    toml_count += 1

shutil.copy("404.html", dist / "404.html")

print(f"\nBuilt {jsonl_count} release index(es) and {toml_count} registration file(s) into dist/")
