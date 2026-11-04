#!/usr/bin/env python3
"""Test export_package/check_link parsing and transformations. Missing global

defines/includes can change ABI layouts; host-path flags break relocation.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_package as ep  # noqa: E402


class GroupArgsTest(unittest.TestCase):
    def test_option_arguments_stay_with_option(self):
        """Verify option arguments stay with option."""
        tokens = ["-o", "a.obj", "-include", "/x/autoconfig.h", "-MD", "-MT", "t", "-MF", "f", "-Os"]
        self.assertEqual(ep.group_args(tokens),
                         ["-o a.obj", "-include /x/autoconfig.h", "-MD", "-MT t", "-MF f", "-Os"])


class CommonArgsTest(unittest.TestCase):
    def test_component_private_items_are_excluded(self):
        """Verify component private items are excluded."""
        a = ["-DGLOBAL=1", "-DPRIVATE_A", "-I/sdk/inc", "-Os"]
        b = ["-DGLOBAL=1", "-I/sdk/inc", "-I/sdk/private_b", "-Os"]
        self.assertEqual(ep.common_args([a, b]), ["-DGLOBAL=1", "-I/sdk/inc", "-Os"])

    def test_portable_flags_drop_paths_and_language_standard(self):
        """Verify portable flags drop paths and language standard."""
        args = ["-march=rv32imafc_zicsr_zifencei", "-fshort-enums", "-std=gnu99", "-c",
                "-fmacro-prefix-map=/home/u/sdk=.", "-ffile-prefix-map=/home/u/work=/work",
                "-o x.obj", "-include /b/autoconfig.h",
                "-I/sdk/inc", "-DX", "-fstack-usage", "-Os"]
        self.assertEqual(ep.portable_flags(args), ["-march=rv32imafc_zicsr_zifencei", "-fshort-enums", "-Os"])


class PortableFlagsTest(unittest.TestCase):
    def test_sdk_warning_policy_not_exported(self):
        """Verify sdk warning policy not exported."""
        self.assertEqual(ep.portable_flags(["-Wall", "-Wundef", "-save-temps=obj", "-fshort-enums"]),
                         ["-fshort-enums"])


class CopyHeadersTest(unittest.TestCase):
    def test_nested_build_dir_skipped(self):
        """Verify nested build dir skipped."""
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "proj"
            (proj / "build" / "generated").mkdir(parents=True)
            (proj / "FreeRTOSConfig.h").write_text("")
            (proj / "build" / "generated" / "autoconfig.h").write_text("")
            out = Path(d) / "out"
            ep.copy_headers(str(proj), out, set(), skip_dirs=[str(proj / "build")])
            self.assertTrue((out / "FreeRTOSConfig.h").is_file())
            self.assertFalse((out / "build").exists())


class HostPathTest(unittest.TestCase):
    """Host-dependent output prevents hash-based rebuild checks and exposes user directory

    names.
    """

    def test_autoconfig_sdk_paths_become_relative(self):
        """Verify autoconfig sdk paths become relative."""
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "autoconfig.h"
            f.write_text('#define CONFIG_BOARD_BOOT2FILE "/w/build/p.sdk/bsp/board/boot2.bin"\n#define BOARD "dk"\n')
            ep.relativize_sdk_paths(f, "/w/build/p.sdk")
            self.assertEqual(f.read_text(),
                             '#define CONFIG_BOARD_BOOT2FILE "bsp/board/boot2.bin"\n#define BOARD "dk"\n')

    def test_host_path_in_any_exported_file_fails(self):
        """Verify host path in any exported file fails."""
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "lib").mkdir()
            (out / "lib" / "libx.a").write_bytes(b"!<arch>\n\x00/w/build/p/board/x.c\x00")
            (out / "include.h").write_text("#define A 1\n")
            with self.assertRaisesRegex(ValueError, "lib/libx.a"):
                ep.check_no_host_paths(out, ["/w/build/p", "/w/toolchain"])
            (out / "lib" / "libx.a").write_bytes(b"!<arch>\n\x00/work/build/p/board/x.c\x00")
            ep.check_no_host_paths(out, ["/w/build/p", "/w/toolchain"])

    def test_strip_keeps_symbols_and_is_deterministic(self):
        """Verify strip keeps symbols and is deterministic."""
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / "lib"
            lib.mkdir()
            for n in ("liba.a", "libb.a"):
                (lib / n).write_bytes(b"")
            log = Path(d) / "args"
            strip = Path(d) / "strip"
            strip.write_text('#!/bin/sh\necho "$@" >> "%s"\n' % log)
            strip.chmod(0o755)
            ep.strip_debug(str(strip), lib)
            calls = log.read_text().splitlines()
            self.assertEqual(calls, ["-D --strip-debug %s" % (lib / n) for n in ("liba.a", "libb.a")])


class EntryArgsTest(unittest.TestCase):
    def test_compiler_and_source_removed(self):
        """Verify compiler and source removed."""
        entry = {"file": "/sdk/a.c", "command": "gcc -DX -o a.obj -c /sdk/a.c"}
        self.assertEqual(ep.entry_args(entry), ["-DX", "-o a.obj", "-c"])


class RspQuoteTest(unittest.TestCase):
    def test_gcc_reads_back_the_same_define(self):
        """Use real GCC to verify response-file quoting preserves defines containing spaces

        and quotes.
        """
        gcc = os.environ.get("BL_TEST_GCC")
        if not gcc:
            self.skipTest("set BL_TEST_GCC to a gcc to run this check")
        # compile_commands arguments after shlex parsing.
        define = '-DBFLB_GIT_SUFFIX=" f924fbb2"'
        with tempfile.TemporaryDirectory() as d:
            rsp = Path(d) / "defines"
            ep.write_rsp(rsp, [define])
            src = Path(d) / "t.c"
            src.write_text("const char *s = BFLB_GIT_SUFFIX;\n")
            out = subprocess.run([gcc, "@" + str(rsp), "-E", str(src)],
                                 capture_output=True, text=True, check=True).stdout
        self.assertIn('const char *s = " f924fbb2";', out)

    def test_plain_flag_unchanged(self):
        """Verify plain flag unchanged."""
        self.assertEqual(ep.rsp_quote("-march=rv32imafc_zicsr_zifencei"), "-march=rv32imafc_zicsr_zifencei")


class ParseLinkTest(unittest.TestCase):
    LINK = ("riscv64-unknown-elf-gcc -T/b/generated/linker.ld -Wl,-Map=/b/x.map -Wl,--cref "
            "-Wl,--gc-sections -nostartfiles -Wl,--print-memory-usage -Wl,--no-relax "
            "--specs=picolibc.specs -ufw_header -Wl,-wrap,snprintf -L/sdk/macsw "
            "-march=rv32imafc_zicsr_zifencei CMakeFiles/x.elf.dir/main.c.obj -o build_out/x.elf "
            "-Wl,--whole-archive /sdk/phyrf/libphy.a build_out/lib/libapp.a "
            "-Wl,--no-whole-archive -lc -lm")

    def test_split_into_archives_flags_and_dirs(self):
        """Verify split into archives flags and dirs."""
        archives, ld_flags, libs, dirs = ep.parse_link(self.LINK, "/b")
        self.assertEqual(archives, ["/sdk/phyrf/libphy.a", "/b/build_out/lib/libapp.a"])
        self.assertEqual(ld_flags, ["-Wl,--gc-sections", "-nostartfiles", "-Wl,--no-relax",
                                    "--specs=picolibc.specs", "-ufw_header", "-Wl,-wrap,snprintf",
                                    "-march=rv32imafc_zicsr_zifencei"])
        self.assertEqual(libs, ["-lc", "-lm"])
        self.assertEqual(dirs, ["/sdk/macsw"])


class LinkerScriptTest(unittest.TestCase):
    def test_include_directives(self):
        """Verify include directives."""
        text = "SECTIONS {\n  INCLUDE macsw_cache_affinity.ld.in\n}\n/* INCLUDE no */\n"
        self.assertEqual(ep.linker_includes(text), ["macsw_cache_affinity.ld.in"])


class MapIncludeDirTest(unittest.TestCase):
    ROOTS = [("/w/sdk", "sdk"), ("/w/build/p/build", "build"), ("/w/build/p", "profile")]

    def test_build_dir_wins_over_enclosing_profile_dir(self):
        """Verify build dir wins over enclosing profile dir."""
        self.assertEqual(ep.map_include_dir("/w/build/p/build/generated", self.ROOTS), "build/generated")

    def test_dot_suffix_normalized(self):
        """Verify dot suffix normalized."""
        self.assertEqual(ep.map_include_dir("/w/sdk/bsp/board/x/.", self.ROOTS), "sdk/bsp/board/x")
        self.assertEqual(ep.map_include_dir("/w/build/p/.", self.ROOTS), "profile")

    def test_unknown_root_rejected(self):
        """Verify unknown root rejected."""
        with self.assertRaises(ValueError):
            ep.map_include_dir("/home/user/private", self.ROOTS)


class DepfileTest(unittest.TestCase):
    def test_headers_from_continuation_lines(self):
        """Verify headers from continuation lines."""
        text = ("x.c.obj: \\\n /s/a.c \\\n /b/generated/autoconfig.h \\\n"
                " /s/inc/./FreeRTOS.h /s/x.inc\n")
        self.assertEqual(ep.depfile_headers(text),
                         ["/b/generated/autoconfig.h", "/s/inc/FreeRTOS.h", "/s/x.inc"])


class DepfileEscapeTest(unittest.TestCase):
    def test_escaped_space_and_crlf(self):
        """Do not silently omit headers when paths contain spaces or dependency files use

        CRLF.
        """
        text = "x.obj: /w/my\\ sdk/a.h \\\r\n /w/b$$.h\r\n"
        self.assertEqual(ep.depfile_headers(text), ["/w/my sdk/a.h", "/w/b$.h"])


def syms(strong=(), weak=(), undef=(), local=()):
    """Build a symbol-table fixture with explicit strong, weak, undefined and local entries."""
    return {"strong": set(strong), "weak": set(weak), "undef": set(undef), "local": set(local)}


class ParseNmTest(unittest.TestCase):
    def test_kinds_and_symbolless_members(self):
        """Verify kinds and symbolless members."""
        text = ("/p/liba.a[x.o]: f T 0 4\n/p/liba.a[x.o]: g W 0 4\n/p/liba.a[x.o]: h U\n"
                "/p/liba.a[x.o]: v d 0 4\n/p/liba.a[x.o]: .LC0 r 0 4\n")
        self.assertEqual(ep.parse_nm(text, ["x.o", "empty.o"]),
                         {"x.o": syms(["f"], ["g"], ["h"], ["v"]), "empty.o": syms()})

    def test_duplicate_member_names_rejected(self):
        """Verify duplicate member names rejected."""
        with self.assertRaises(ValueError):
            ep.parse_nm("", ["a.o", "a.o"])


class ForcedMembersTest(unittest.TestCase):
    """SDK whole-archive makes these members active; selective package linking must force

    them to avoid silent fallback implementations.
    """

    TOOLCHAIN = {"libc.a": {"nano-malloc.c.o": syms(["malloc"]),
                            "libc_time_time.c.o": syms(["time"], undef=["gettimeofday"])}}

    def test_libc_port_overrides_picolibc_for_every_program(self):
        """Verify libc port overrides picolibc for every program."""
        package = {"liblibc.a": {"port_memory.c.obj": syms(["malloc"], undef=["kmalloc"]),
                                 "lib_only.c.obj": syms(["sdk_only_fn"])}}
        forced = ep.forced_members(package, self.TOOLCHAIN, {"wifi": ["libphy.a"]})
        self.assertEqual(forced, {"base": [("liblibc.a", "port_memory.c.obj",
                                            "malloc 也由 libc.a(nano-malloc.c.o) 定义")]})

    def test_os_hook_for_toolchain_libc_is_forced(self):
        """g++ moves -lc after the package group, so picolibc time() can only find the SDK

        gettimeofday() if the member providing it is always linked, even as a weak definition.
        """
        package = {"liblibc.a": {"port_time.c.obj": syms(["_gettimeofday_r"], weak=["gettimeofday"])}}
        forced = ep.forced_members(package, self.TOOLCHAIN, {})
        self.assertEqual(forced, {"base": [("liblibc.a", "port_time.c.obj",
                                            "gettimeofday 由工具链库引用但未定义")]})

    def test_override_needing_wifi_library_goes_to_wifi_group(self):
        """Regulatory tables depend on wireless libraries; placing them in base would pull

        Wi-Fi into non-Wi-Fi sketches.
        """
        package = {
            "libapp.a": {"wireless_regdb.c.obj": syms(["wireless_regdb_init"], undef=["wireless_config_set_reg_db"])},
            "libphy.a": {"wireless_config.c.obj": syms(["wireless_config_set_reg_db"], ["wireless_regdb_init"])},
        }
        forced = ep.forced_members(package, {}, {"wifi": ["libphy.a"]})
        self.assertEqual(list(forced), ["wifi"])
        self.assertEqual(forced["wifi"][0][:2], ("libapp.a", "wireless_regdb.c.obj"))

    def test_local_only_member_forced_but_empty_member_not(self):
        """Verify local only member forced but empty member not."""
        package = {"liblhal.a": {"lhal_version.c.obj": syms(local=["version_lhal"]), "disabled.c.obj": syms()}}
        forced = ep.forced_members(package, {}, {})
        self.assertEqual(forced, {"base": [("liblhal.a", "lhal_version.c.obj", "只有局部定义")]})


class CheckReplaceableTest(unittest.TestCase):
    def test_unrelated_directory_is_not_deleted(self):
        """Verify unrelated directory is not deleted."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes.txt").write_text("keep")
            with self.assertRaises(ValueError):
                ep.check_replaceable(Path(d))

    def test_previous_export_and_empty_dir_allowed(self):
        """Verify previous export and empty dir allowed."""
        with tempfile.TemporaryDirectory() as d:
            ep.check_replaceable(Path(d))
            (Path(d) / "manifest.json").write_text('{"name": "x-libs", "version": "0", "files": []}')
            ep.check_replaceable(Path(d))


class SdkTreeTest(unittest.TestCase):
    """Patched packages must come from a tree with exactly the declared patches, keeping

    manifest provenance aligned with library contents.
    """

    def test_patches_require_build_tree(self):
        """Verify patches require build tree."""
        pinned = {"commit": "c" * 40, "patches": [{"file": "patches/x.patch", "sha256": "0" * 64}]}
        with self.assertRaises(ValueError):
            ep.sdk_tree(None, Path("/sdk"), pinned)

    def test_without_patches_checkout_is_used(self):
        """Verify without patches checkout is used."""
        self.assertEqual(ep.sdk_tree(None, Path("/sdk"), {"commit": "c" * 40}), Path("/sdk"))

    def test_tree_marker_must_match(self):
        """Verify tree marker must match."""
        import prepare_sdk_tree as pst
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / pst.MARKER).write_text(json.dumps({"commit": "c" * 40, "patches": []}))
            self.assertEqual(ep.sdk_tree(d, Path("/sdk"), {"commit": "c" * 40}), Path(d).resolve())
            with self.assertRaises(ValueError):
                ep.sdk_tree(d, Path("/sdk"), {"commit": "c" * 40,
                                               "patches": [{"file": "patches/x.patch", "sha256": "0" * 64}]})


class MapMembersTest(unittest.TestCase):
    def test_members_with_content_and_forced_names_restored(self):
        """Verify members with content and forced names restored."""
        import check_link
        text = ("由于文件 (符号) 而包含了归档成员\n/p/lib/liblibc.a(x.o)\n\n"
                "LOAD /p/lib/libforce_base.a\n"
                ".text           0x0000000023000000     0x100\n"
                " .text.a        0x0000000023000000       0x1c /p/lib/libforce_base.a(liblibc.a@port_memory.c.obj)\n"
                " .text.very_long_function_name\n"
                "                0x0000000023000020       0x10 /p/lib/liblhal.a(bflb_flash.c.obj)\n"
                " .text.empty    0x0000000023000030        0x0 /p/lib/libstd.a(empty.o)\n"
                " .debug_info    0x0000000000000000       0x40 /p/lib/libstd.a(debug_only.o)\n")
        self.assertEqual(check_link.map_members(text),
                         {("liblibc.a", "port_memory.c.obj"), ("liblhal.a", "bflb_flash.c.obj")})


class PostProcTest(unittest.TestCase):
    def test_boot_files_from_log(self):
        """Verify boot files from log."""
        log = ("bflb_fw_post_proc-ubuntu --chipname=bl616cl --imgfile=/b/x.bin --appkeys=shared "
               "--boot2file=/s/boot2.bin --ptfile=/s/pt.toml --dtsfile=/s/a.dts\n")
        self.assertEqual(ep.post_proc_files(log),
                         {"boot2": "/s/boot2.bin", "partition": "/s/pt.toml", "dts": "/s/a.dts"})


if __name__ == "__main__":
    unittest.main()
