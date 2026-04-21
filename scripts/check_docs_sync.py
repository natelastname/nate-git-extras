#!/usr/bin/env python3

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src_root = ROOT / "src"
docs_root = ROOT / "docs" / "files"

missing = []

for py_file in src_root.rglob("*.py"):
    rel = py_file.relative_to(ROOT)
    expected = docs_root / (str(rel).replace("/", "_") + ".md")
    if not expected.exists():
        missing.append((rel, expected.relative_to(ROOT)))

if missing:
    print("Missing file docs:")
    for src, doc in missing:
        print(f"  {src} -> {doc}")
    sys.exit(1)

print("Documentation sync check passed.")
