#!/usr/bin/env python3
"""从 SDK 构建目录导出 arduino-bl 底层包。

输入是 build.sh 产生的 profile 构建目录（CMake 构建根目录为 <build>/build）。
输出目录结构：

  include/   全部编译单元共有的 include 目录中的头文件，按 sdk/、build/、profile/ 分类
  lib/       链接命令中的全部静态库，以及 libforce_<组>.a（见下）
  ld/        预处理后的 linker.ld 及其 INCLUDE 的片段
  boot/      镜像后处理使用的 boot2、分区表、dts
  flags/     defines、includes、c_flags、S_flags、ld_flags、ld_libs、ld_libs_<组>，供 gcc @file 使用
  manifest.json

includes 使用 -iwithprefixbefore 相对路径，使用方需在其前传入 -iprefix <包>/include/。
autoconfig.h 必须以 -include 注入所有编译单元，路径见 manifest 的 forced_include。

SDK 以 --whole-archive 链接全部组件库，包不这样做，以免每个程序都带入网络栈。
因此只靠 --whole-archive 才会生效的成员（覆盖工具链 libc 或弱定义的实现、只有局部定义的成员）
被单独打成 libforce_<组>.a，由 ld_libs 或 ld_libs_<组> 以 --whole-archive 链入。
组由 profile 的 link_groups.json 定义；不属于任何组的成员归入 base，所有程序都会链入。
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
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

# nm -P 的符号类型：全局强定义、弱定义、未定义、局部定义
STRONG_TYPES = set("TDBRSGA")
WEAK_TYPES = set("WV")
LOCAL_TYPES = set("tdbrsg")
NM_LINE = re.compile(r"^.*\[(?P<member>[^\]]+)\]: (?P<sym>\S+) (?P<type>\S)")

# --specs=nano.specs 时 gcc 隐式链接的库；SDK 的部分组件库覆盖其中的符号
NANO_TOOLCHAIN_LIBS = ("libc_nano.a", "libm_nano.a", "libgloss_nano.a", "libgcc.a")

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
    """仓库的 HEAD 与是否有未提交改动；不是 git 仓库时报错。"""
    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout
    return {"commit": git("rev-parse", "HEAD").strip(), "dirty": git("status", "--porcelain").strip() != ""}


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
    """解析 gcc -MD 依赖文件，返回其中的头文件路径；处理续行、CRLF、转义空格与 $$。"""
    body = text.replace("\r\n", "\n").replace("\\\n", " ")
    paths = []
    for line in body.splitlines():
        if ":" not in line:
            continue
        for tok in re.findall(r"(?:\\.|[^\s\\])+", line.split(":", 1)[1]):
            path = re.sub(r"\\(.)", r"\1", tok).replace("$$", "$")
            if Path(path).suffix in HEADER_SUFFIXES:
                paths.append(os.path.normpath(path))
    return paths


def post_proc_files(log_text):
    m = re.search(r"--boot2file=(\S+)\s+--ptfile=(\S+)\s+--dtsfile=(\S+)", log_text)
    if not m:
        raise ValueError("post-process command not found in build log")
    return {"boot2": m.group(1), "partition": m.group(2), "dts": m.group(3)}


def parse_nm(text, members):
    """解析 nm -A -P 的输出；members 为 ar t 列出的成员，没有符号的成员也保留。

    返回 {成员: {"strong": 集合, "weak": 集合, "undef": 集合, "local": 集合}}。
    local 不含汇编器生成的 .L 标号。
    """
    if len(members) != len(set(members)):
        raise ValueError("duplicate archive member names")
    table = {m: {"strong": set(), "weak": set(), "undef": set(), "local": set()} for m in members}
    for line in text.splitlines():
        m = NM_LINE.match(line)
        if not m:
            continue
        t, sym = m.group("type"), m.group("sym")
        if t in STRONG_TYPES:
            kind = "strong"
        elif t in WEAK_TYPES:
            kind = "weak"
        elif t == "U":
            kind = "undef"
        elif t in LOCAL_TYPES and not sym.startswith(".L"):
            kind = "local"
        else:
            continue
        table[m.group("member")][kind].add(sym)
    return table


def archive_symbols(tools, path):
    members = subprocess.run([tools["ar"], "t", path], capture_output=True, text=True, check=True).stdout.split()
    nm = subprocess.run([tools["nm"], "-A", "-P", path], capture_output=True, text=True, check=True).stdout
    return parse_nm(nm, members)


def forced_members(package, toolchain, groups):
    """找出只靠 --whole-archive 才会生效的包内成员，按链接组分开。

    package、toolchain：{归档名: parse_nm() 结果}；groups：{组名: [归档名通配]}。
    需要强制链入的成员：
    - 强定义的符号在链接集合的其他成员中也有定义。不强制链入时，工具链 libc 或别处的弱定义
      可能先满足引用，例如 newlib 的 malloc 取代 SDK 接到 TLSF 堆的 _malloc_r。
    - 没有全局定义、但有局部定义。这类成员只能经 --whole-archive 进入，内容靠链接脚本 KEEP 保留，
      例如放进 .version.* 段的版本字符串。配置关闭后为空的成员不强制。
    成员所在归档属于某组，或它的某个未定义符号只能由同一组的归档提供时，归入该组，其余归入 base。
    返回 {组名: [(归档名, 成员, 原因)]}。
    """
    providers = defaultdict(set)
    for libs in (package, toolchain):
        for lib, table in libs.items():
            for member, syms in table.items():
                for s in syms["strong"] | syms["weak"]:
                    providers[s].add((lib, member))

    def group_of(lib):
        return next((g for g, pats in groups.items() if any(fnmatch.fnmatch(lib, p) for p in pats)), None)

    result = defaultdict(list)
    for lib in sorted(package):
        for member, syms in sorted(package[lib].items()):
            dup = sorted(s for s in syms["strong"] if len(providers[s]) > 1)
            if dup:
                other = sorted(providers[dup[0]] - {(lib, member)})[0]
                reason = "%s 也由 %s(%s) 定义" % (dup[0], other[0], other[1])
            elif not syms["strong"] and not syms["weak"] and syms["local"]:
                reason = "只有局部定义"
            else:
                continue
            group = group_of(lib)
            if group is None:
                for s in sorted(syms["undef"]):
                    owners = {group_of(l) for l, _ in providers.get(s, ())}
                    if len(owners) == 1 and None not in owners:
                        group = owners.pop()
                        break
            result[group or "base"].append((lib, member, reason))
    return dict(result)


def write_forced_archive(tools, archives, entries, dest):
    """取出成员原样打包，成员名为“原归档名@原成员名”；ar 的 D 选项使结果与时间和用户无关。"""
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for lib, member, _ in entries:
            f = Path(tmp) / ("%s@%s" % (lib, member))
            f.write_bytes(subprocess.run([tools["ar"], "p", archives[lib], member],
                                         capture_output=True, check=True).stdout)
            files.append(str(f))
        subprocess.run([tools["ar"], "rcsD", str(dest)] + files, check=True)


def toolchain_libs(gcc, ld_flags):
    """gcc 按 ld_flags 的多库选项隐式链接的库的路径。"""
    if "--specs=nano.specs" not in ld_flags:
        raise ValueError("only --specs=nano.specs links are supported")
    multilib = [f for f in ld_flags if f.startswith(("-march=", "-mabi="))]
    libs = {}
    for name in NANO_TOOLCHAIN_LIBS:
        path = subprocess.run([gcc] + multilib + ["-print-file-name=" + name],
                              capture_output=True, text=True, check=True).stdout.strip()
        if not os.path.isabs(path) or not os.path.isfile(path):
            raise ValueError("toolchain library not found: " + name)
        libs[name] = os.path.normpath(path)
    return libs


def is_previous_export(path):
    try:
        manifest = json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and {"name", "version", "files"} <= manifest.keys()


def check_replaceable(out):
    """out 只能不存在、是空目录或本工具以前的导出，避免误删其他目录。"""
    if out.exists() and not (out.is_dir() and (not any(out.iterdir()) or is_previous_export(out))):
        raise ValueError("refusing to replace %s: not empty and not a previous export" % out)


def export(args, write=None):
    """由 write(args, 临时目录) 导出到 out 旁的临时目录，成功后再替换 out；失败时 out 保持不变。"""
    out = Path(args.out).resolve()
    check_replaceable(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=out.name + ".tmp-", dir=out.parent))
    try:
        manifest = (write or write_package)(args, tmp)
        check_replaceable(out)
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest


def write_package(args, out):
    project = Path(args.build).resolve()
    cmake_dir = project / "build"
    sdk = Path(args.sdk).resolve()
    sources = json.loads(Path(args.sources).read_text())
    sdk_state = git_state(sdk)
    if sdk_state != {"commit": sources["sdk"]["commit"], "dirty": False}:
        raise ValueError("SDK checkout %s does not match sources.json: %r" % (sdk, sdk_state))
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
    for dep in cmake_dir.rglob("*.d"):
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

    # 只靠 --whole-archive 生效的成员
    compiler = os.path.basename((entries[0]["arguments"] if "arguments" in entries[0]
                                 else shlex.split(entries[0]["command"]))[0])
    if not compiler.endswith("gcc"):
        raise ValueError("unexpected compiler: " + compiler)
    tools = {t: str(Path(args.toolchain_bin) / (compiler[:-3] + t)) for t in ("gcc", "ar", "nm")}
    groups = json.loads((project / "link_groups.json").read_text())
    if "base" in groups:
        raise ValueError("link group name 'base' is reserved")
    package_archives = {os.path.basename(a): a for a in archives}
    forced = forced_members({n: archive_symbols(tools, a) for n, a in package_archives.items()},
                            {n: archive_symbols(tools, a) for n, a in toolchain_libs(tools["gcc"], ld_flags).items()},
                            groups)
    whole = {}
    for group, members in forced.items():
        name = "libforce_%s.a" % group
        if (out / "lib" / name).exists():
            raise ValueError("duplicate library name: " + name)
        write_forced_archive(tools, package_archives, members, out / "lib" / name)
        whole[group] = ["-Wl,--whole-archive", "-lforce_" + group, "-Wl,--no-whole-archive"]
    write_rsp(out / "flags" / "ld_libs", whole.get("base", []) + lib_names + system_libs)
    for group in groups:
        write_rsp(out / "flags" / ("ld_libs_" + group), whole.get(group, []))

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
        "link": {
            "groups": {g: {"archives": pats, "ld_libs": "flags/ld_libs_" + g} for g, pats in groups.items()},
            "forced": {g: [{"archive": l, "member": m, "reason": r} for l, m, r in e] for g, e in forced.items()},
        },
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
    p.add_argument("--toolchain-bin", required=True, help="构建所用工具链的 bin 目录")
    p.add_argument("--sources", required=True, help="sources.json")
    p.add_argument("--profile", required=True, help="<chip>/<profile>")
    p.add_argument("--version", required=True)
    p.add_argument("--out", required=True, help="输出目录；已存在时只替换空目录或以前的导出")
    manifest = export(p.parse_args(argv))
    print("exported %s %s: %d files" % (manifest["name"], manifest["version"], len(manifest["files"])))


if __name__ == "__main__":
    sys.exit(main())
