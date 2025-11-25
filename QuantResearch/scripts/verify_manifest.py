#!/usr/bin/env python3
"""Verify that manifest entries exist and match file hashes."""

from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify data/_manifest.json contents.")
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = args.manifest
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as fh:
        entries = json.load(fh)
    errors = []
    for entry in entries:
        rel = entry.get("path")
        if not rel:
            continue
        file_path = (REPO_ROOT / rel).resolve()
        if not file_path.exists():
            errors.append(f"Missing file: {rel}")
            continue
        expected = entry.get("sha256")
        actual = sha256(file_path)
        if expected and expected != actual:
            errors.append(f"Hash mismatch for {rel}: manifest={expected} actual={actual}")
    if errors:
        print("[verify_manifest] FAIL:")
        for err in errors:
            print(" -", err)
        raise SystemExit(1)
    print("[verify_manifest] OK - all files match manifest")


if __name__ == "__main__":
    main()
