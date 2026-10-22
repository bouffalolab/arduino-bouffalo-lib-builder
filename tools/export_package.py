#!/usr/bin/env python3
"""从 SDK 构建目录导出 arduino-bl 底层包。

输入是 build.sh 产生的 profile 构建目录（CMake 构建根目录为 <build>/build）。
输出目录结构：

  include/   全部编译单元共有的 include 目录中的头文件，按 sdk/、build/、profile/ 分类
  lib/       链接命令中的全部静态库
  ld/        预处理后的 linker.ld 及其 INCLUDE 的片段
  boot/      镜像后处理使用的 boot2、分区表、dts
  flags/     defines、includes、c_flags、S_flags、ld_flags、ld_libs，供 gcc @file 使用
  manifest.json

includes 使用 -iwithprefixbefore 相对路径，使用方需在其前传入 -iprefix <包>/include/。
autoconfig.h 必须以 -include 注入所有编译单元，路径见 manifest 的 forced_include。
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HEADER_SUFFIXES = {".h", ".hpp", ".inc"}

# 后面紧跟一个独立参数的编译选项
ARG_OPTIONS = {"-o", "-include", "-MT", "-MF", "-MQ", "-isystem", "-x"}

# 与路径、输出或语言标准相关，不进入包的编译选项
DROP_PREFIXES = (
    "-I",
    "-D",
    "-o ",
    "-include ",
    "-MT ",
    "-MF ",
    "-MQ ",
    "-fmacro-prefix-map=",
    "-fdebug-prefix-map=",
    "-fdiagnostics-color",
    "-std=",
    "-W",            # 警告级别由平台决定
    "-save-temps",
)
DROP_EXACT = {"-c", "-MD", "-MMD", "-fstack-usage"}

# 链接命令中不进入 ld_flags 的选项
LD_DROP_PREFIXES = ("-T", "-Wl,-Map=", "-L", "-l")
LD_DROP_EXACT = {
    "-Wl,--cref",
    "-Wl,--print-memory-usage",
    "-Wl,--whole-archive",
    "-Wl,--no-whole-archive",
}


def group_args(tokens):
    """把需要独立参数的选项与其参数合并为一个元素，如 ["-o x"]。"""
    out = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ARG_OPTIONS and i + 1 < len(tokens):
            out.append(tok + " " + tokens[i + 1])
            i += 2
        else:
            out.append(tok)
            i += 1
    return out


def entry_args(entry):
    """compile_commands 条目去掉编译器和源文件后的参数列表。"""
    tokens = entry["arguments"] if "arguments" in entry else shlex.split(entry["command"])
    args = group_args(tokens[1:])
    source = entry["file"]
    return [a for a in args if a != source and os.path.normpath(a) != os.path.normpath(source)]


def common_args(arg_lists):
    """所有列表共有的参数，按第一个列表中的顺序。"""
    if not arg_lists:
        return []
    rest = [set(a) for a in arg_lists[1:]]
    seen = set()
    out = []
    for arg in arg_lists[0]:
        if arg in seen:
            continue
        if all(arg in s for s in rest):
            out.append(arg)
            seen.add(arg)
    return out


def portable_flags(args):
    return [a for a in args if a not in DROP_EXACT and not a.startswith(DROP_PREFIXES)]


def rsp_quote(arg):
    """按 gcc 响应文件规则转义：空白、引号和反斜杠前加反斜杠。"""
    return re.sub(r"([\s'\"\\])", r"\\\1", arg)


def write_rsp(path, args):
    path.write_text("".join(rsp_quote(a) + "\n" for a in args))


def parse_link(text, cwd):
    """解析 CMake link.txt，返回 (archives, ld_flags, system_libs, lib_dirs)。"""
    tokens = group_args(shlex.split(text))[1:]
    archives, ld_flags, system_libs, lib_dirs = [], [], [], []
    for tok in tokens:
        if tok.endswith(".a"):
            archives.append(os.path.normpath(os.path.join(cwd, tok)))
        elif tok.startswith("-l"):
            system_libs.append(tok)
        elif tok.startswith("-L"):
            lib_dirs.append(tok[2:])
        elif tok.startswith("-o ") or tok.endswith((".obj", ".o")):
            continue
        elif tok in LD_DROP_EXACT or tok.startswith(LD_DROP_PREFIXES):
            continue
        else:
            ld_flags.append(tok)
    return archives, ld_flags, system_libs, lib_dirs


def linker_includes(text):
    """链接脚本中 INCLUDE 的文件名。"""
    return re.findall(r"^\s*INCLUDE\s+(\S+)", text, flags=re.MULTILINE)


def map_include_dir(path, roots):
    """把绝对 include 目录映射为包内相对路径；roots 为 [(绝对根, 包内前缀)]。"""
    path = os.path.normpath(path)
    for root, prefix in roots:
        root = os.path.normpath(root)
        if path == root or path.startswith(root + os.sep):
            rel = os.path.relpath(path, root)
            return prefix if rel == "." else prefix + "/" + rel
    raise ValueError("include directory outside known roots: " + path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state(repo):
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError:
        head = None
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip() != ""
    return {"commit": head, "dirty": dirty}


def copy_headers(src_dir, dst_dir, copied, skip_dirs=()):
    """递归复制头文件；skip_dirs 中的目录（如嵌套在工程目录里的 CMake 构建目录）不进入。"""
    skip = {os.path.normpath(d) for d in skip_dirs}
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [n for n in dirnames if os.path.normpath(os.path.join(dirpath, n)) not in skip]
        for name in filenames:
            if Path(name).suffix not in HEADER_SUFFIXES:
                continue
            src = Path(dirpath) / name
            dst = dst_dir / src.relative_to(src_dir)
            if dst in copied:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.add(dst)


def depfile_headers(text):
    """解析 gcc -MD 依赖文件，返回其中的头文件路径。"""
    body = text.replace("\\\n", " ")
    paths = []
    for line in body.splitlines():
        if ":" not in line:
            continue
        for tok in line.split(":", 1)[1].split():
            if Path(tok).suffix in HEADER_SUFFIXES:
                paths.append(os.path.normpath(tok))
    return paths


def post_proc_files(log_text):
    m = re.search(r"--boot2file=(\S+)\s+--ptfile=(\S+)\s+--dtsfile=(\S+)", log_text)
    if not m:
        raise ValueError("post-process command not found in build log")
    return {"boot2": m.group(1), "partition": m.group(2), "dts": m.group(3)}


def export(args):
    project = Path(args.build).resolve()
    cmake_dir = project / "build"
    sdk = Path(args.sdk).resolve()
    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    for sub in ("include", "lib", "ld", "boot", "flags"):
        (out / sub).mkdir(parents=True)

    roots = [(str(sdk), "sdk"), (str(cmake_dir), "build"), (str(project), "profile")]

    # 编译参数
    entries = json.loads((cmake_dir / "compile_commands.json").read_text())
    all_args = [entry_args(e) for e in entries]
    c_args = [a for e, a in zip(entries, all_args) if e["file"].endswith(".c")]
    s_args = [a for e, a in zip(entries, all_args) if e["file"].endswith((".S", ".s"))]
    # 全局 defines 和 include 目录以 C 编译单元的交集为准，汇编单元可能缺少部分全局项
    shared = common_args(c_args)

    defines = [a for a in shared if a.startswith("-D")]
    include_dirs = [a[2:] for a in shared if a.startswith("-I")]
    forced = [a.split(" ", 1)[1] for a in shared if a.startswith("-include ")]
    if len(forced) != 1:
        raise ValueError("expected exactly one forced include, got %r" % forced)

    copied = set()
    include_rel = []
    for d in include_dirs:
        rel = map_include_dir(d, roots)
        include_rel.append(rel)
        # SDK 根目录也在全局 include 路径中，递归会带入整个 SDK；它只按依赖文件补齐
        if os.path.normpath(d) == str(sdk):
            continue
        copy_headers(d, out / "include" / rel, copied, skip_dirs=[str(cmake_dir)])

    # 补齐构建实际用到、但不在上面目录中的 SDK 头文件
    for dep in cmake_dir.rglob("*.obj.d"):
        for h in depfile_headers(dep.read_text(errors="replace")):
            if not h.startswith(str(sdk) + os.sep):
                continue
            dst = out / "include" / "sdk" / os.path.relpath(h, sdk)
            if dst not in copied and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(h, dst)
                copied.add(dst)
    forced_rel = "include/" + map_include_dir(os.path.dirname(forced[0]), roots) + "/" + os.path.basename(forced[0])
    if not (out / forced_rel).is_file():
        raise ValueError("forced include not exported: " + forced_rel)

    write_rsp(out / "flags" / "defines", defines)
    write_rsp(out / "flags" / "includes", [x for rel in include_rel for x in ("-iwithprefixbefore", rel)])
    write_rsp(out / "flags" / "c_flags", portable_flags(common_args(c_args)))
    write_rsp(out / "flags" / "S_flags", portable_flags(common_args(s_args)))

    # 链接参数与库
    link_txt = next(cmake_dir.glob("CMakeFiles/*.elf.dir/link.txt"))
    archives, ld_flags, system_libs, lib_dirs = parse_link(link_txt.read_text(), cmake_dir)
    lib_names = []
    for a in archives:
        name = os.path.basename(a)
        if (out / "lib" / name).exists():
            raise ValueError("duplicate library name: " + name)
        shutil.copy2(a, out / "lib" / name)
        lib_names.append("-l" + name[3:-2])
    write_rsp(out / "flags" / "ld_flags", ld_flags)
    write_rsp(out / "flags" / "ld_libs", lib_names + system_libs)

    # 链接脚本与 INCLUDE 片段
    ld_src = cmake_dir / "generated" / "linker.ld"
    shutil.copy2(ld_src, out / "ld" / "linker.ld")
    pending = linker_includes(ld_src.read_text())
    done = set()
    while pending:
        name = pending.pop()
        if name in done:
            continue
        found = next((Path(d) / name for d in [str(ld_src.parent)] + lib_dirs if (Path(d) / name).is_file()), None)
        if found is None:
            raise ValueError("linker script INCLUDE not found: " + name)
        shutil.copy2(found, out / "ld" / name)
        done.add(name)
        pending.extend(linker_includes(found.read_text()))

    # 镜像后处理资源
    boot = {}
    for key, src in post_proc_files(Path(args.build_log).read_text(errors="replace")).items():
        shutil.copy2(src, out / "boot" / os.path.basename(src))
        boot[key] = "boot/" + os.path.basename(src)

    shutil.copy2(sdk / "LICENSE", out / "LICENSE.bouffalo_sdk")

    sources = json.loads(Path(args.sources).read_text())
    chip, profile = args.profile.split("/")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {
        "name": chip + "-libs",
        "version": args.version,
        "chip": chip,
        "profile": profile,
        "sdk": sources["sdk"],
        "toolchain": sources["toolchain"],
        "builder": git_state(Path(__file__).resolve().parent.parent),
        "forced_include": forced_rel,
        "post_process": {"chipname": chip, "appkeys": "shared", **boot},
        "licenses": {
            "LICENSE.bouffalo_sdk": "bouffalo_sdk 根目录许可（Apache-2.0）；预编译无线库与工具的许可未单独核对",
        },
        "files": [
            {"path": str(p.relative_to(out)), "size": p.stat().st_size, "sha256": sha256_file(p)}
            for p in files
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description="从 SDK 构建目录导出 arduino-bl 底层包。")
    p.add_argument("--build", required=True, help="profile 构建目录，如 work/build/bl616cl-default")
    p.add_argument("--build-log", required=True, help="build.sh 生成的构建日志")
    p.add_argument("--sdk", required=True, help="SDK 检出目录")
    p.add_argument("--sources", required=True, help="sources.json")
    p.add_argument("--profile", required=True, help="<chip>/<profile>")
    p.add_argument("--version", required=True)
    p.add_argument("--out", required=True, help="输出目录，已存在时先删除")
    manifest = export(p.parse_args(argv))
    print("exported %s %s: %d files" % (manifest["name"], manifest["version"], len(manifest["files"])))


if __name__ == "__main__":
    sys.exit(main())
