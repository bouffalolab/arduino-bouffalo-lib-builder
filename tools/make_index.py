#!/usr/bin/env python3
"""Generate a local Boards Manager index and archives for installation tests. Inputs select

platform, libs, flash-tools, sources, pinned toolchain archive, base URL and output.
Emit platform HEAD as arduino-bl-<version>.tar.gz, manifest-versioned dependency/tool
packages, the unchanged pinned toolchain archive, and package_bouffalolab_index.json.
Sort members and clear timestamps/owners for deterministic archive identity. Emit Linux
x86_64 systems only.
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
    """Parse Arduino key=value text, ignoring blank lines and hash comments."""
    props = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    return props


def board_names(boards_txt):
    """Extract display names from the platform board definitions."""
    props = parse_properties(boards_txt)
    return [props[k] for k in sorted(props) if k.count(".") == 1 and k.endswith(".name")]


def tar_dir(src, root, dest):
    """Create a tar.gz with one root directory, retaining modes while clearing timestamps

    and ownership.
    """
    def reset(info):
        """Normalize tar member ownership, names and timestamps for deterministic archives."""
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
    """Describe an archive with its URL, size and SHA-256 for the package index."""
    return {
        "url": base_url.rstrip("/") + "/" + path.name,
        "archiveFileName": path.name,
        "checksum": "SHA-256:" + sha256_file(path),
        "size": str(path.stat().st_size),
    }


def git(repo, *args):
    """Run Git in the explicitly selected source repository."""
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_index(args):
    """Build the package index and archives from the requested fixed inputs."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    platform = Path(args.platform)

    # Archive platform HEAD only; exclude uncommitted changes.
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
    """Parse options and run the make index validation or packaging command."""
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
