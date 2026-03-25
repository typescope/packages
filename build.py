#!/usr/bin/env python3
"""Build the flat registry for Cloudflare Pages.

Flattens releases/<shard>/<namespace>/<package>.jsonl
        → dist/<package>.jsonl
"""

import shutil
from pathlib import Path

dist = Path("dist")
if dist.exists():
    shutil.rmtree(dist)
dist.mkdir()

copied = 0
for jsonl in sorted(Path("releases").rglob("*.jsonl")):
    dest = dist / jsonl.name
    if dest.exists():
        print(f"CONFLICT: {jsonl.name} appears more than once under releases/")
        raise SystemExit(1)
    shutil.copy(jsonl, dest)
    print(f"  {jsonl} -> dist/{jsonl.name}")
    copied += 1

print(f"\nBuilt {copied} package(s) into dist/")
