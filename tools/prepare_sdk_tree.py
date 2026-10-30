#!/usr/bin/env python3
"""Prepare a build SDK tree from a clean sources.json-pinned checkout, including ignored-

file cleanliness. Hardlink tracked files, but detach patch targets before applying
ordered sdk.patches with checked SHA256 and -p1. Verify the checkout again afterward.
Stage beside out and replace only on success; allow only absent/empty/recognized
outputs. MARKER records source commit and patches for export verification. Point GIT_DIR
at the checkout and GIT_WORK_TREE at out so SDK-generated version strings retain the
patched dirty marker. Remove obsolete patches when updating to an upstream version
containing them.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = ".arduino-bl-sdk-tree.json"


def git(repo, *args):
    """Run Git in the explicitly selected source repository."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def check_checkout(sdk, commit):
    """Require the source checkout to match its pinned revision and cleanliness policy."""
    head = git(sdk, "rev-parse", "HEAD").strip()
    if head != commit:
        raise ValueError("SDK HEAD %s != pinned %s" % (head, commit))
    status = git(sdk, "status", "--porcelain", "--ignored")
    if status.strip():
        raise ValueError("SDK checkout is not clean: %s\n%s" % (sdk, status[:2000]))


def tracked_files(sdk):
    """List tracked regular files and symlinks; reject submodules, which cannot be copied

    as hardlinks.
    """
    files = []
    for entry in git(sdk, "ls-files", "-s", "-z").split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        if meta.split()[0] == "160000":
            raise ValueError("SDK submodule not supported: " + path)
        files.append(path)
    return files


def patch_targets(text):
    """Return SDK-relative patch targets, removing the leading a/ or b/ component."""
    targets = set()
    for line in text.splitlines():
        if line.startswith(("--- ", "+++ ")):
            path = line[4:].split("\t", 1)[0].strip()
            if path != "/dev/null":
                targets.add(path.split("/", 1)[1])
    if not targets:
        raise ValueError("patch has no file headers")
    return sorted(targets)


def unshare(path):
    """Replace a hardlink with an independent identical file before modification so the

    checkout remains unchanged.
    """
    tmp = path.with_name(path.name + ".unshare")
    shutil.copy2(path, tmp)
    os.replace(tmp, path)


def link_tree(sdk, files, dst):
    """Create an isolated SDK source tree from the selected tracked files."""
    for rel in files:
        src, out = sdk / rel, dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), out)
        else:
            os.link(src, out)


def apply_patch(tree, patch):
    """Apply one expected patch in the isolated SDK tree."""
    base = ["patch", "-p1", "--batch", "--forward", "--no-backup-if-mismatch", "--reject-file=-", "-d", str(tree),
            "-i", str(patch)]
    for extra in (["--dry-run"], []):
        r = subprocess.run(base + extra, capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError("patch %s does not apply:\n%s" % (patch, (r.stdout + r.stderr)[-2000:]))


def check_replaceable(out):
    """Reject an output path that is not an eligible previous export."""
    if out.exists() and not (out.is_dir() and (not any(out.iterdir()) or (out / MARKER).is_file())):
        raise ValueError("refusing to replace %s: not empty and not a previous SDK tree" % out)


def prepare(sdk, sources_path, out):
    """Prepare and validate the requested build inputs without modifying the installed package."""
    sdk, sources_path, out = Path(sdk).resolve(), Path(sources_path).resolve(), Path(out).resolve()
    pinned = json.loads(sources_path.read_text())["sdk"]
    patches = pinned.get("patches", [])
    check_checkout(sdk, pinned["commit"])
    check_replaceable(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=out.name + ".tmp-", dir=out.parent))
    try:
        files = tracked_files(sdk)
        link_tree(sdk, files, tmp)
        applied = []
        for p in patches:
            path = sources_path.parent / p["file"]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != p["sha256"]:
                raise ValueError("patch %s sha256 %s != %s in sources.json" % (p["file"], digest, p["sha256"]))
            for rel in patch_targets(path.read_text()):
                if (tmp / rel).is_symlink():
                    raise ValueError("patch %s modifies symlink %s" % (p["file"], rel))
                if (tmp / rel).exists():
                    unshare(tmp / rel)
            apply_patch(tmp, path)
            applied.append({"file": p["file"], "sha256": digest})
        (tmp / MARKER).write_text(json.dumps({"commit": pinned["commit"], "patches": applied}, indent=1) + "\n")
        # Hardlinks share content; a changed checkout indicates a patch wrote through a link that was not detached.
        check_checkout(sdk, pinned["commit"])
        check_replaceable(out)
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"files": len(files), "patches": applied}


def read_marker(tree):
    """Read the prepared SDK marker; fail for directories not generated by this tool."""
    path = Path(tree) / MARKER
    if not path.is_file():
        raise ValueError("%s is not an SDK tree from prepare_sdk_tree.py" % tree)
    return json.loads(path.read_text())


def check_tree(tree, pinned):
    """Require prepared-tree commit and patches to match sources.json SDK metadata."""
    want = {"commit": pinned["commit"],
            "patches": [{"file": p["file"], "sha256": p["sha256"]} for p in pinned.get("patches", [])]}
    marker = read_marker(tree)
    if marker != want:
        raise ValueError("SDK tree %s does not match sources.json: %r != %r" % (tree, marker, want))


def main(argv=None):
    """Parse options and run the prepare sdk tree validation or packaging command."""
    p = argparse.ArgumentParser(description="由 SDK 检出生成构建用的 SDK 目录并应用补丁。")
    p.add_argument("--sdk", required=True, help="SDK 检出目录")
    p.add_argument("--sources", required=True, help="sources.json")
    p.add_argument("--out", required=True, help="输出目录；已存在时只替换空目录或以前生成的目录")
    a = p.parse_args(argv)
    result = prepare(a.sdk, a.sources, a.out)
    print("prepared %s: %d files, patches: %s" % (a.out, result["files"],
                                                  ", ".join(x["file"] for x in result["patches"]) or "none"))


if __name__ == "__main__":
    sys.exit(main())
