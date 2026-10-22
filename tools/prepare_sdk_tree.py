#!/usr/bin/env python3
"""由 SDK 检出生成构建用的 SDK 目录，并应用 sources.json 中的补丁。

  prepare_sdk_tree.py --sdk <SDK 检出> --sources sources.json --out <目录>

检出目录不能有任何改动或新增文件（含被忽略的文件），HEAD 必须是 sources.json 固定的 commit。
<out> 中是检出里全部已跟踪文件的硬链接，不另占空间；补丁要改的文件先换成独立拷贝再打补丁，
检出目录因此保持不变，完成后再核对一次。

补丁列在 sources.json 的 sdk.patches，每项 {"file": 相对 sources.json 的路径, "sha256": ...}，
按顺序以 -p1 应用于 SDK 根目录。补丁合入上游并随新版 SDK 发布后，升级 sources.json 时一并删除。

先在 <out> 旁的临时目录生成，成功后替换 <out>；<out> 只能不存在、为空目录或是以前生成的目录。
<out>/MARKER 记录 commit 与已应用的补丁，export_package.py 据此确认包与构建所用的 SDK 一致。
构建时 SDK 用 git 生成版本信息，应设 GIT_DIR=<检出>/.git、GIT_WORK_TREE=<out>，打过补丁时版本带 dirty 标记。
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
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def check_checkout(sdk, commit):
    head = git(sdk, "rev-parse", "HEAD").strip()
    if head != commit:
        raise ValueError("SDK HEAD %s != pinned %s" % (head, commit))
    status = git(sdk, "status", "--porcelain", "--ignored")
    if status.strip():
        raise ValueError("SDK checkout is not clean: %s\n%s" % (sdk, status[:2000]))


def tracked_files(sdk):
    """已跟踪的普通文件与符号链接；子模块无法用硬链接复制，直接报错。"""
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
    """补丁改动的文件（相对 SDK 根目录，去掉 a/、b/ 一级）。"""
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
    """把硬链接换成内容相同的独立文件，之后修改它不会影响检出目录。"""
    tmp = path.with_name(path.name + ".unshare")
    shutil.copy2(path, tmp)
    os.replace(tmp, path)


def link_tree(sdk, files, dst):
    for rel in files:
        src, out = sdk / rel, dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), out)
        else:
            os.link(src, out)


def apply_patch(tree, patch):
    base = ["patch", "-p1", "--batch", "--forward", "--no-backup-if-mismatch", "--reject-file=-", "-d", str(tree),
            "-i", str(patch)]
    for extra in (["--dry-run"], []):
        r = subprocess.run(base + extra, capture_output=True, text=True)
        if r.returncode != 0:
            raise ValueError("patch %s does not apply:\n%s" % (patch, (r.stdout + r.stderr)[-2000:]))


def check_replaceable(out):
    if out.exists() and not (out.is_dir() and (not any(out.iterdir()) or (out / MARKER).is_file())):
        raise ValueError("refusing to replace %s: not empty and not a previous SDK tree" % out)


def prepare(sdk, sources_path, out):
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
        # 硬链接共享内容：检出目录若被改动，说明补丁写到了未拆开的链接上
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
    """构建所用 SDK 目录的标记；不是本工具生成的目录时报错。"""
    path = Path(tree) / MARKER
    if not path.is_file():
        raise ValueError("%s is not an SDK tree from prepare_sdk_tree.py" % tree)
    return json.loads(path.read_text())


def check_tree(tree, pinned):
    """构建所用 SDK 目录的提交与补丁必须与 sources.json 的 sdk 一致。"""
    want = {"commit": pinned["commit"],
            "patches": [{"file": p["file"], "sha256": p["sha256"]} for p in pinned.get("patches", [])]}
    marker = read_marker(tree)
    if marker != want:
        raise ValueError("SDK tree %s does not match sources.json: %r != %r" % (tree, marker, want))


def main(argv=None):
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
