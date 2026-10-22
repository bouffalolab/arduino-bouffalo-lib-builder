#!/usr/bin/env python3
"""从 SDK 检出导出 Arduino 工具包 bl-flash-tools：镜像后处理与串口烧录程序。

只导出 Linux x86_64 程序。输出目录结构：

  bflb_fw_post_proc/    bflb_fw_post_proc-ubuntu 及其 usage.txt、version_info.txt
  bouffalo_flash_cube/  BLFlashCommand-ubuntu、version_info.txt、ReleaseNote_En.txt、chips/<芯片>/
  LICENSE.bouffalo_sdk
  manifest.json

两个程序都是 PyInstaller 单文件程序，各自按自身所在目录查找配置。
BLFlashCommand 每次运行都在自身目录下创建 log/，目录不可写时直接退出，所以安装目录必须可写；
bflb_fw_post_proc 只写 --imgfile 所在目录。
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_package import export, git_state, sha256_file  # noqa: E402

TOOLS_DIR = "tools/bflb_tools"
FILES = [
    "bflb_fw_post_proc/bflb_fw_post_proc-ubuntu",
    "bflb_fw_post_proc/usage.txt",
    "bflb_fw_post_proc/version_info.txt",
    "bouffalo_flash_cube/BLFlashCommand-ubuntu",
    "bouffalo_flash_cube/version_info.txt",
    "bouffalo_flash_cube/ReleaseNote_En.txt",
]


def tool_files(tools_dir, chips):
    """要导出的文件（相对 tools_dir）：固定程序与所选芯片的烧录配置；缺任何一项都报错。"""
    rels = list(FILES)
    for chip in chips:
        chip_dir = tools_dir / "bouffalo_flash_cube" / "chips" / chip
        if not chip_dir.is_dir():
            raise ValueError("chip config not found: %s" % chip_dir)
        rels += sorted(str(p.relative_to(tools_dir)) for p in chip_dir.rglob("*") if p.is_file())
    for rel in rels:
        if not (tools_dir / rel).is_file():
            raise ValueError("tool file not found: %s" % (tools_dir / rel))
    return rels


def write_tools(args, out):
    sdk = Path(args.sdk).resolve()
    sources = json.loads(Path(args.sources).read_text())
    sdk_state = git_state(sdk)
    if sdk_state != {"commit": sources["sdk"]["commit"], "dirty": False}:
        raise ValueError("SDK checkout %s does not match sources.json: %r" % (sdk, sdk_state))

    tools_dir = sdk / TOOLS_DIR
    for rel in tool_files(tools_dir, args.chip):
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tools_dir / rel, out / rel)  # 保留可执行权限
    shutil.copy2(sdk / "LICENSE", out / "LICENSE.bouffalo_sdk")

    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {
        "name": "bl-flash-tools",
        "version": args.version,
        "host": "x86_64-linux-gnu",
        "chips": args.chip,
        "sdk": sources["sdk"],
        "source_dir": TOOLS_DIR,
        "builder": git_state(Path(__file__).resolve().parent.parent),
        "licenses": {
            "LICENSE.bouffalo_sdk": "bouffalo_sdk 根目录许可（Apache-2.0）；bflb_tools 中的程序没有单独的许可声明，再分发前需确认",
        },
        "files": [
            {"path": str(p.relative_to(out)), "size": p.stat().st_size, "sha256": sha256_file(p)}
            for p in files
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description="从 SDK 检出导出 bl-flash-tools 工具包。")
    p.add_argument("--sdk", required=True, help="SDK 检出目录")
    p.add_argument("--sources", required=True, help="sources.json")
    p.add_argument("--chip", required=True, action="append", help="导出其烧录配置的芯片，可重复")
    p.add_argument("--version", required=True)
    p.add_argument("--out", required=True, help="输出目录；已存在时只替换空目录或以前的导出")
    args = p.parse_args(argv)
    manifest = export(args, write_tools)
    print("exported %s %s: %d files" % (manifest["name"], manifest["version"], len(manifest["files"])))


if __name__ == "__main__":
    sys.exit(main())
