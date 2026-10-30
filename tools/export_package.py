#!/usr/bin/env python3
"""Export an arduino-bl dependency package from the profile build produced by build.sh;

CMake lives under <build>/build. Output include/ with SDK/build/profile headers, lib/
with stripped static and forced-member archives, ld/ with linker scripts/fragments,
boot/ with image resources, flags/ with GCC response files, and manifest.json. Pair
-iwithprefixbefore relative includes with -iprefix <package>/include/ and force
autoconfig.h into every unit. Reject host paths. Unlike SDK whole-archive linking,
retain only required overrides/local-only metadata through libforce_<group>.a.
link_groups.json defines optional groups; unmatched members belong to the base group
linked by every sketch.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_sdk_tree import check_tree  # noqa: E402

HEADER_SUFFIXES = {".h", ".hpp", ".inc"}

# Compiler options followed by a separate argument.
ARG_OPTIONS = {"-o", "-include", "-MT", "-MF", "-MQ", "-isystem", "-x"}

# Path/output/language-standard options excluded from package compile flags.
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
    "-ffile-prefix-map=",
    "-fdiagnostics-color",
    "-std=",
    "-W",            # The platform selects warning levels.
    "-save-temps",
)
DROP_EXACT = {"-c", "-MD", "-MMD", "-fstack-usage"}

# nm -P symbol kinds: strong global, weak, undefined and local definitions.
STRONG_TYPES = set("TDBRSGA")
WEAK_TYPES = set("WV")
LOCAL_TYPES = set("tdbrsg")
NM_LINE = re.compile(r"^.*\[(?P<member>[^\]]+)\]: (?P<sym>\S+) (?P<type>\S)")

# Libraries implicitly linked by GCC with nano.specs; SDK components override some symbols.
NANO_TOOLCHAIN_LIBS = ("libc_nano.a", "libm_nano.a", "libgloss_nano.a", "libgcc.a")

# Link-command options excluded from ld_flags.
LD_DROP_PREFIXES = ("-T", "-Wl,-Map=", "-L", "-l")
LD_DROP_EXACT = {
    "-Wl,--cref",
    "-Wl,--print-memory-usage",
    "-Wl,--whole-archive",
    "-Wl,--no-whole-archive",
}


def group_args(tokens):
    """Group options with separate arguments into one element, for example '-o x'."""
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
    """Return compile_commands arguments without the compiler executable or source

    filename.
    """
    tokens = entry["arguments"] if "arguments" in entry else shlex.split(entry["command"])
    args = group_args(tokens[1:])
    source = entry["file"]
    return [a for a in args if a != source and os.path.normpath(a) != os.path.normpath(source)]


def common_args(arg_lists):
    """Return arguments common to every list, preserving first-list order."""
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
    """Remove build-local paths and compile-only options from exported flags."""
    return [a for a in args if a not in DROP_EXACT and not a.startswith(DROP_PREFIXES)]


def rsp_quote(arg):
    """Quote a GCC response-file argument by escaping whitespace, quotes and backslashes."""
    return re.sub(r"([\s'\"\\])", r"\\\1", arg)


def write_rsp(path, args):
    """Write GCC response-file arguments with one escaped argument per line."""
    path.write_text("".join(rsp_quote(a) + "\n" for a in args))


def parse_link(text, cwd):
    """Parse CMake link.txt into archives, linker flags, system libraries and library

    directories.
    """
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
    """Return filenames referenced by linker-script INCLUDE directives."""
    return re.findall(r"^\s*INCLUDE\s+(\S+)", text, flags=re.MULTILINE)


def map_include_dir(path, roots):
    """Map an absolute include directory to a package-relative path using absolute-

    root/package-prefix pairs.
    """
    path = os.path.normpath(path)
    for root, prefix in roots:
        root = os.path.normpath(root)
        if path == root or path.startswith(root + os.sep):
            rel = os.path.relpath(path, root)
            return prefix if rel == "." else prefix + "/" + rel
    raise ValueError("include directory outside known roots: " + path)


def sha256_file(path):
    """Return a streamed SHA-256 digest of the supplied file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state(repo):
    """Return repository HEAD and dirty state; fail if the directory is not a Git

    repository.
    """
    def git(*a):
        """Run Git in the explicitly selected source repository."""
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout
    return {"commit": git("rev-parse", "HEAD").strip(), "dirty": git("status", "--porcelain").strip() != ""}


def copy_headers(src_dir, dst_dir, copied, skip_dirs=()):
    """Copy headers recursively, excluding skip_dirs such as nested CMake build

    directories.
    """
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
    """Parse GCC -MD header dependencies, handling continuations, CRLF, escaped spaces and

    doubled dollars.
    """
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
    """Extract image-resource paths from the SDK post-processing log."""
    m = re.search(r"--boot2file=(\S+)\s+--ptfile=(\S+)\s+--dtsfile=(\S+)", log_text)
    if not m:
        raise ValueError("post-process command not found in build log")
    return {"boot2": m.group(1), "partition": m.group(2), "dts": m.group(3)}


def parse_nm(text, members):
    """Parse nm -A -P output, retaining every ar-listed member even without symbols. Return

    per-member strong/weak/undefined/local symbol sets; exclude assembler-generated .L
    labels from local definitions.
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
    """Read global/local symbol information from the selected archive."""
    members = subprocess.run([tools["ar"], "t", path], capture_output=True, text=True, check=True).stdout.split()
    nm = subprocess.run([tools["nm"], "-A", "-P", path], capture_output=True, text=True, check=True).stdout
    return parse_nm(nm, members)


def forced_members(package, toolchain, groups):
    """Find package members needing forced inclusion, grouped by link dependencies. Inputs

    map archive names to parsed symbols and groups to archive globs. Force strong
    definitions also defined elsewhere, preventing toolchain libc/weak fallbacks from
    satisfying them first (for example SDK TLSF _malloc_r). Also force local-only
    members retained by linker KEEP, such as version metadata, but not empty disabled
    objects. Assign members by archive group or exclusive unresolved-symbol dependency;
    otherwise use base. Return group -> (archive, member, reason) entries.
    """
    providers = defaultdict(set)
    for libs in (package, toolchain):
        for lib, table in libs.items():
            for member, syms in table.items():
                for s in syms["strong"] | syms["weak"]:
                    providers[s].add((lib, member))

    def group_of(lib):
        """Select the optional link group associated with an archive."""
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
    """Extract and rearchive members unchanged as original-archive@member; ar D removes

    timestamp/user dependence.
    """
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for lib, member, _ in entries:
            f = Path(tmp) / ("%s@%s" % (lib, member))
            f.write_bytes(subprocess.run([tools["ar"], "p", archives[lib], member],
                                         capture_output=True, check=True).stdout)
            files.append(str(f))
        subprocess.run([tools["ar"], "rcsD", str(dest)] + files, check=True)


def toolchain_libs(gcc, ld_flags):
    """Return GCC implicit library paths selected by the ld_flags multilib options."""
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


def relativize_sdk_paths(path, tree):
    """Make SDK Kconfig default BOOT2FILE/DTSFILE/PTFILE paths relative in autoconfig.h.

    These values serve SDK CMake flashing and are not referenced by C code.
    """
    text = path.read_text()
    path.write_text(text.replace(tree + os.sep, ""))


def strip_debug(strip, lib_dir):
    """Strip library debug information while preserving symbols/LTO and linked image

    contents. Use deterministic archive metadata (-D) so timestamps and uid do not
    change hashes.
    """
    for a in sorted(lib_dir.glob("*.a")):
        subprocess.run([strip, "-D", "--strip-debug", str(a)], capture_output=True, text=True, check=True)


def check_no_host_paths(out, roots):
    """Reject host build paths in all exported files, including archives, to preserve

    relocation/provenance and avoid exposing user directories.
    """
    needles = sorted({r.encode() for r in roots}, key=len, reverse=True)
    bad = []
    for p in sorted(out.rglob("*")):
        if p.is_file():
            data = p.read_bytes()
            hit = next((n for n in needles if n in data), None)
            if hit:
                bad.append("%s: %s" % (p.relative_to(out), hit.decode()))
    if bad:
        raise ValueError("exported files contain build host paths:\n" + "\n".join(bad[:20]))


def is_previous_export(path):
    """Identify an existing package directory eligible for controlled replacement."""
    try:
        manifest = json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and {"name", "version", "files"} <= manifest.keys()


def sdk_tree(tree_arg, sdk, pinned):
    """Return the actual build SDK tree; pinned patches require a tree produced by

    prepare_sdk_tree.py.
    """
    if tree_arg is None:
        if pinned.get("patches"):
            raise ValueError("sources.json lists SDK patches; pass --sdk-tree with the patched build tree")
        return sdk
    tree = Path(tree_arg).resolve()
    check_tree(tree, pinned)
    return tree


def check_replaceable(out):
    """Allow out only when absent, empty or recognized as a previous export, preventing

    unrelated deletion.
    """
    if out.exists() and not (out.is_dir() and (not any(out.iterdir()) or is_previous_export(out))):
        raise ValueError("refusing to replace %s: not empty and not a previous export" % out)


def export(args, write=None):
    """Export through write(args, temporary-directory) beside out, replacing out only on

    success; failures leave the old export intact.
    """
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
    """Write package libraries, headers, linker resources, flags and source manifest."""
    project = Path(args.build).resolve()
    cmake_dir = project / "build"
    sdk = Path(args.sdk).resolve()
    sources = json.loads(Path(args.sources).read_text())
    sdk_state = git_state(sdk)
    if sdk_state != {"commit": sources["sdk"]["commit"], "dirty": False}:
        raise ValueError("SDK checkout %s does not match sources.json: %r" % (sdk, sdk_state))
    # Map headers/includes against the actual build SDK tree and verify its recorded patches.
    tree = sdk_tree(args.sdk_tree, sdk, sources["sdk"])
    for sub in ("include", "lib", "ld", "boot", "flags"):
        (out / sub).mkdir(parents=True)

    roots = [(str(tree), "sdk"), (str(cmake_dir), "build"), (str(project), "profile")]

    # Compile flags.
    entries = json.loads((cmake_dir / "compile_commands.json").read_text())
    all_args = [entry_args(e) for e in entries]
    c_args = [a for e, a in zip(entries, all_args) if e["file"].endswith(".c")]
    s_args = [a for e, a in zip(entries, all_args) if e["file"].endswith((".S", ".s"))]
    # Derive global defines/includes from C-unit intersections; assembly units may omit some global settings.
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
        # Do not recursively copy the SDK root include directory, which would include the whole SDK; fill it from dependency files only.
        if os.path.normpath(d) == str(tree):
            continue
        copy_headers(d, out / "include" / rel, copied, skip_dirs=[str(cmake_dir)])

    # Add SDK headers actually used but outside the common include directories.
    for dep in cmake_dir.rglob("*.d"):
        for h in depfile_headers(dep.read_text(errors="replace")):
            if not h.startswith(str(tree) + os.sep):
                continue
            dst = out / "include" / "sdk" / os.path.relpath(h, tree)
            if dst not in copied and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(h, dst)
                copied.add(dst)
    forced_rel = "include/" + map_include_dir(os.path.dirname(forced[0]), roots) + "/" + os.path.basename(forced[0])
    if not (out / forced_rel).is_file():
        raise ValueError("forced include not exported: " + forced_rel)
    relativize_sdk_paths(out / forced_rel, str(tree))

    write_rsp(out / "flags" / "defines", defines)
    write_rsp(out / "flags" / "includes", [x for rel in include_rel for x in ("-iwithprefixbefore", rel)])
    write_rsp(out / "flags" / "c_flags", portable_flags(common_args(c_args)))
    write_rsp(out / "flags" / "S_flags", portable_flags(common_args(s_args)))

    # Link flags and libraries.
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

    # Members effective only under --whole-archive.
    compiler = os.path.basename((entries[0]["arguments"] if "arguments" in entries[0]
                                 else shlex.split(entries[0]["command"]))[0])
    if not compiler.endswith("gcc"):
        raise ValueError("unexpected compiler: " + compiler)
    tools = {t: str(Path(args.toolchain_bin) / (compiler[:-3] + t)) for t in ("gcc", "ar", "nm", "strip")}
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

    # Linker script and INCLUDE fragments.
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

    # Image post-processing resources.
    boot = {}
    for key, src in post_proc_files(Path(args.build_log).read_text(errors="replace")).items():
        shutil.copy2(src, out / "boot" / os.path.basename(src))
        boot[key] = "boot/" + os.path.basename(src)

    shutil.copy2(sdk / "LICENSE", out / "LICENSE.bouffalo_sdk")

    if (out / "lib/libarduino_littlefs.a").exists():
        shutil.copy2(sdk / "components/fs/littlefs/littlefs/LICENSE.md", out / "LICENSE.littlefs")

    strip_debug(tools["strip"], out / "lib")
    builder = Path(__file__).resolve().parent.parent
    check_no_host_paths(out, [str(tree), str(sdk), str(project), str(Path(args.toolchain_bin).resolve().parent),
                              str(builder)])

    chip, profile = args.profile.split("/")
    files = sorted(p for p in out.rglob("*") if p.is_file())
    manifest = {
        "name": chip + "-libs",
        "version": args.version,
        "chip": chip,
        "profile": profile,
        "sdk": sources["sdk"],
        "toolchain": sources["toolchain"],
        "builder": git_state(builder),
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
    if (out / "lib/libarduino_littlefs.a").exists():
        manifest["littlefs"] = {
            "source_path": "components/fs/littlefs/littlefs",
            "sdk_commit": sources["sdk"]["commit"],
            "version": "2.11", "disk_version": "2.1",
            "archive": "lib/libarduino_littlefs.a",
            "partition": boot["partition"],
        }
        manifest["licenses"]["LICENSE.littlefs"] = "littlefs BSD-3-Clause"
    controller = next((Path(a) for a in archives
                       if Path(a).name == "libbtblecontroller_bl616cl_uarthci.a"), None)
    if controller is not None:
        manifest["controller"] = {
            "variant": "bl616cl/uarthci",
            "host": "external",
            "source_path": str(controller.relative_to(tree)),
            "source_sha256": sha256_file(controller),
            "archive": "lib/" + controller.name,
            "configuration": forced_rel,
            "license": "LICENSE.bouffalo_sdk",
        }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def main(argv=None):
    """Parse options and run the export package validation or packaging command."""
    p = argparse.ArgumentParser(description="从 SDK 构建目录导出 arduino-bl 底层包。")
    p.add_argument("--build", required=True, help="profile 构建目录，如 work/build/bl616cl-default")
    p.add_argument("--build-log", required=True, help="build.sh 生成的构建日志")
    p.add_argument("--sdk", required=True, help="SDK 检出目录")
    p.add_argument("--sdk-tree", help="构建所用的 SDK 目录（build.sh 生成的 <构建目录>.sdk）；不给时为检出目录，此时不能有补丁")
    p.add_argument("--toolchain-bin", required=True, help="构建所用工具链的 bin 目录")
    p.add_argument("--sources", required=True, help="sources.json")
    p.add_argument("--profile", required=True, help="<chip>/<profile>")
    p.add_argument("--version", required=True)
    p.add_argument("--out", required=True, help="输出目录；已存在时只替换空目录或以前的导出")
    manifest = export(p.parse_args(argv))
    print("exported %s %s: %d files" % (manifest["name"], manifest["version"], len(manifest["files"])))


if __name__ == "__main__":
    sys.exit(main())
