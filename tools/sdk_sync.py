#!/usr/bin/env python3
"""Find bouffalo_sdk releases newer than sources.json and move sdk.ref to one (sdk-sync workflow).

Usage:
  sdk_sync.py check --sources sources.json --repo-url URL   print current=, latest=, update= lines
  sdk_sync.py set --sources sources.json --ref vX.Y.Z      rewrite sdk.ref only

Only release tags vX.Y.Z count. A sources.json ref that is not a release tag (a branch) is
left alone: builds already follow that branch.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def release_tags(ls_remote):
    """Return release tag names from `git ls-remote --tags` output (peeled ^{} lines ignored)."""
    tags = set()
    for line in ls_remote.splitlines():
        ref = line.split("\t")[-1]
        name = ref[len("refs/tags/"):] if ref.startswith("refs/tags/") else ""
        if RELEASE.match(name):
            tags.add(name)
    return tags


def version(tag):
    """Numeric version tuple of a release tag, so v2.3.10 sorts after v2.3.9."""
    return tuple(int(x) for x in tag[1:].split("."))


def newer_release(current, tags):
    """Return the newest release tag newer than current, or None (also None for a branch ref)."""
    if not RELEASE.match(current) or not tags:
        return None
    latest = max(tags, key=version)
    return latest if version(latest) > version(current) else None


def set_ref(sources, ref):
    """Rewrite sdk.ref in sources.json, keeping every other field and the file's JSON layout."""
    if not RELEASE.match(ref):
        raise ValueError("not a release tag: " + ref)
    data = json.loads(sources.read_text())
    data["sdk"]["ref"] = ref
    sources.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(description="Track bouffalo_sdk release tags for sources.json.")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--sources", type=Path, required=True)
    c.add_argument("--repo-url", required=True)
    s = sub.add_parser("set")
    s.add_argument("--sources", type=Path, required=True)
    s.add_argument("--ref", required=True)
    a = p.parse_args(argv)
    if a.cmd == "set":
        set_ref(a.sources, a.ref)
        return
    current = json.loads(a.sources.read_text())["sdk"]["ref"]
    out = subprocess.run(["git", "ls-remote", "--tags", a.repo_url], capture_output=True, text=True,
                         check=True).stdout
    latest = newer_release(current, release_tags(out))
    print("current=%s\nlatest=%s\nupdate=%s" % (current, latest or "", "true" if latest else "false"))


if __name__ == "__main__":
    main()
