#!/usr/bin/env python3
"""Validate pinned source, patch, and toolchain identities."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(root: Path) -> dict:
    source_path = root / "sources.json"
    data = json.loads(source_path.read_text())
    errors: list[str] = []
    sdk = data.get("sdk", {})
    toolchain = data.get("toolchain", {})
    if not sdk.get("repo", "").startswith("https://"):
        errors.append("sdk.repo must use HTTPS")
    if not HEX40.fullmatch(sdk.get("commit", "")) or set(sdk.get("commit", "")) == {"0"}:
        errors.append("sdk.commit must be a nonzero 40-character SHA-1")
    if not sdk.get("ref"):
        errors.append("sdk.ref is required as provenance")
    patches = sdk.get("patches", [])
    if not patches:
        errors.append("sdk.patches must not be empty")
    patch_records = []
    for item in patches:
        path = root / item.get("file", "")
        expected = item.get("sha256", "")
        if not path.is_file():
            errors.append(f"missing patch: {item.get('file')}")
            continue
        actual = sha256(path)
        patch_records.append({"file": item["file"], "sha256": actual})
        if not HEX64.fullmatch(expected) or set(expected) == {"0"} or actual != expected:
            errors.append(f"patch SHA mismatch: {item.get('file')}")
    if not toolchain.get("url", "").startswith("https://"):
        errors.append("toolchain.url must use HTTPS")
    if not HEX64.fullmatch(toolchain.get("sha256", "")) or set(toolchain.get("sha256", "")) == {"0"}:
        errors.append("toolchain.sha256 must be a nonzero 64-character SHA-256")
    if not toolchain.get("dir"):
        errors.append("toolchain.dir is required")
    result = {
        "schema": "arduino-bl-source-lock/v1",
        "sdk": {"repo": sdk.get("repo"), "ref": sdk.get("ref"), "commit": sdk.get("commit"), "patches": patch_records},
        "toolchain": {"name": toolchain.get("name"), "version": toolchain.get("version"), "url": toolchain.get("url"), "sha256": toolchain.get("sha256"), "dir": toolchain.get("dir")},
        "errors": errors,
    }
    if errors:
        raise ValueError("; ".join(errors))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.root.resolve())
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
