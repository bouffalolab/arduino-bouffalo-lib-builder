#!/usr/bin/env python3
"""生成本地 Boards Manager 包索引与归档，用于验证安装链。

  make_index.py --platform <arduino-bl 仓库> --libs <底层包目录> --flash-tools <烧录工具包目录>
                --sources sources.json --toolchain-tarball <工具链 tar.xz> --base-url <URL> --out <目录>

输出：
  arduino-bl-<版本>.tar.gz          平台仓库 HEAD 的 git archive，版本取自 HEAD 的 platform.txt
  <包名>-<版本>.tar.gz              底层包与烧录工具包，版本取自各自 manifest.json
  <工具链 tar.xz>                    sources.json 固定的上游工具链归档，原样复制
  package_bouffalolab_index.json    归档 URL 为 <base-url>/<文件名>

归档内容按名称排序，时间戳、属主清零，同样输入得到同样的 SHA-256。只生成 Linux x86_64 条目。
"""

import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_package import sha256_file  # noqa: E402

HOST = "x86_64-linux-gnu"
PACKAGER = "bouffalolab"
ARCH = "bl"
TOOLCHAIN_NAME = "xuantie-gcc-riscv"


def parse_properties(text):
    """Arduino 的 key=value 文本；忽略空行与 # 注释。"""
    props = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    return props


def board_names(boards_txt):
    props = parse_properties(boards_txt)
    return [props[k] for k in sorted(props) if k.count(".") == 1 and k.endswith(".name")]


def tar_dir(src, root, dest):
    """把 src 打成 dest（tar.gz），归档内只有一个顶层目录 root；保留权限位，清除时间与属主。"""
    def reset(info):
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    paths = sorted(Path(src).rglob("*"))
    with open(dest, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.add(src, arcname=root, recursive=False, filter=reset)
        for p in paths:
            tar.add(p, arcname=root + "/" + str(p.relative_to(src)), recursive=False, filter=reset)


def archive_entry(path, base_url):
    return {
        "url": base_url.rstrip("/") + "/" + path.name,
        "archiveFileName": path.name,
        "checksum": "SHA-256:" + sha256_file(path),
        "size": str(path.stat().st_size),
    }


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_index(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    platform = Path(args.platform)

    # 平台：HEAD 的内容，未提交的改动不进入归档
    version = parse_properties(git(platform, "show", "HEAD:platform.txt"))["version"]
    platform_archive = out / ("arduino-bl-%s.tar.gz" % version)
    subprocess.run(["git", "-C", str(platform), "archive", "--format=tar.gz",
                    "--prefix=arduino-bl-%s/" % version, "-o", str(platform_archive.resolve()), "HEAD"], check=True)

    tools = []
    for src in (Path(args.libs), Path(args.flash_tools)):
        manifest = json.loads((src / "manifest.json").read_text())
        root = "%s-%s" % (manifest["name"], manifest["version"])
        dest = out / (root + ".tar.gz")
        tar_dir(src, root, dest)
        tools.append((manifest["name"], manifest["version"], dest))

    toolchain = json.loads(Path(args.sources).read_text())["toolchain"]
    tarball = Path(args.toolchain_tarball)
    if sha256_file(tarball) != toolchain["sha256"]:
        raise ValueError("toolchain tarball does not match sources.json: %s" % tarball)
    shutil.copy2(tarball, out / tarball.name)
    tools.append((TOOLCHAIN_NAME, toolchain["version"], out / tarball.name))

    index = {"packages": [{
        "name": PACKAGER,
        "maintainer": "Bouffalo Lab",
        "websiteURL": "https://github.com/bouffalolab",
        "email": "",
        "help": {"online": "https://github.com/bouffalolab"},
        "platforms": [{
            "name": parse_properties(git(platform, "show", "HEAD:platform.txt"))["name"],
            "architecture": ARCH,
            "version": version,
            "category": "Contributed",
            "help": {"online": "https://github.com/bouffalolab"},
            **archive_entry(platform_archive, args.base_url),
            "boards": [{"name": n} for n in board_names(git(platform, "show", "HEAD:boards.txt"))],
            "toolsDependencies": [{"packager": PACKAGER, "name": n, "version": v} for n, v, _ in tools],
        }],
        "tools": [{"name": n, "version": v, "systems": [{"host": HOST, **archive_entry(p, args.base_url)}]}
                  for n, v, p in tools],
    }]}
    (out / "package_bouffalolab_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n")
    return {"platform_commit": git(platform, "rev-parse", "HEAD").strip(), "index": index}


def main(argv=None):
    p = argparse.ArgumentParser(description="生成本地 Boards Manager 包索引与归档。")
    p.add_argument("--platform", required=True, help="arduino-bl 仓库，归档其 HEAD")
    p.add_argument("--libs", required=True, help="底层包目录，如 work/out/bl616cl-libs")
    p.add_argument("--flash-tools", required=True, help="烧录工具包目录，如 work/out/bl-flash-tools")
    p.add_argument("--sources", required=True, help="sources.json")
    p.add_argument("--toolchain-tarball", required=True, help="sources.json 固定的工具链归档")
    p.add_argument("--base-url", required=True, help="归档所在 URL，如 http://127.0.0.1:8765")
    p.add_argument("--out", required=True, help="输出目录，必须不存在")
    result = make_index(p.parse_args(argv))
    print("platform %s at %s" % (result["index"]["packages"][0]["platforms"][0]["version"], result["platform_commit"]))


if __name__ == "__main__":
    sys.exit(main())
