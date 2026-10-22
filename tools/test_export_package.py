#!/usr/bin/env python3
"""export_package.py 与 check_link.py 的解析与转换测试。

这些规则决定 Arduino 编译能否得到与预编译库一致的配置：
漏掉全局 define 或 include 会让结构体布局不一致，带入路径相关参数会破坏可重定位。
"""

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
        tokens = ["-o", "a.obj", "-include", "/x/autoconfig.h", "-MD", "-MT", "t", "-MF", "f", "-Os"]
        self.assertEqual(ep.group_args(tokens),
                         ["-o a.obj", "-include /x/autoconfig.h", "-MD", "-MT t", "-MF f", "-Os"])


class CommonArgsTest(unittest.TestCase):
    def test_component_private_items_are_excluded(self):
        a = ["-DGLOBAL=1", "-DPRIVATE_A", "-I/sdk/inc", "-Os"]
        b = ["-DGLOBAL=1", "-I/sdk/inc", "-I/sdk/private_b", "-Os"]
        self.assertEqual(ep.common_args([a, b]), ["-DGLOBAL=1", "-I/sdk/inc", "-Os"])

    def test_portable_flags_drop_paths_and_language_standard(self):
        args = ["-march=rv32imafc_xtheade", "-fshort-enums", "-std=gnu99", "-c",
                "-fmacro-prefix-map=/home/u/sdk=.", "-o x.obj", "-include /b/autoconfig.h",
                "-I/sdk/inc", "-DX", "-fstack-usage", "-Os"]
        self.assertEqual(ep.portable_flags(args), ["-march=rv32imafc_xtheade", "-fshort-enums", "-Os"])


class PortableFlagsTest(unittest.TestCase):
    def test_sdk_warning_policy_not_exported(self):
        self.assertEqual(ep.portable_flags(["-Wall", "-Wundef", "-save-temps=obj", "-fshort-enums"]),
                         ["-fshort-enums"])


class CopyHeadersTest(unittest.TestCase):
    def test_nested_build_dir_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "proj"
            (proj / "build" / "generated").mkdir(parents=True)
            (proj / "FreeRTOSConfig.h").write_text("")
            (proj / "build" / "generated" / "autoconfig.h").write_text("")
            out = Path(d) / "out"
            ep.copy_headers(str(proj), out, set(), skip_dirs=[str(proj / "build")])
            self.assertTrue((out / "FreeRTOSConfig.h").is_file())
            self.assertFalse((out / "build").exists())


class EntryArgsTest(unittest.TestCase):
    def test_compiler_and_source_removed(self):
        entry = {"file": "/sdk/a.c", "command": "gcc -DX -o a.obj -c /sdk/a.c"}
        self.assertEqual(ep.entry_args(entry), ["-DX", "-o a.obj", "-c"])


class RspQuoteTest(unittest.TestCase):
    def test_gcc_reads_back_the_same_define(self):
        """用真实 gcc 读取响应文件，确认带引号和空格的 define 不被改变。"""
        gcc = os.environ.get("BL_TEST_GCC")
        if not gcc:
            self.skipTest("set BL_TEST_GCC to a gcc to run this check")
        # compile_commands 经 shlex 解析后的 argv 形式
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
        self.assertEqual(ep.rsp_quote("-march=rv32imafc_xtheade"), "-march=rv32imafc_xtheade")


class ParseLinkTest(unittest.TestCase):
    LINK = ("riscv64-unknown-elf-gcc -T/b/generated/linker.ld -Wl,-Map=/b/x.map -Wl,--cref "
            "-Wl,--gc-sections -nostartfiles -Wl,--print-memory-usage -Wl,--no-relax "
            "--specs=nano.specs -ufw_header -Wl,-wrap,snprintf -L/sdk/macsw "
            "-march=rv32imafc_xtheade CMakeFiles/x.elf.dir/main.c.obj -o build_out/x.elf "
            "-Wl,--whole-archive /sdk/phyrf/libphy.a build_out/lib/libapp.a "
            "-Wl,--no-whole-archive -lc -lm")

    def test_split_into_archives_flags_and_dirs(self):
        archives, ld_flags, libs, dirs = ep.parse_link(self.LINK, "/b")
        self.assertEqual(archives, ["/sdk/phyrf/libphy.a", "/b/build_out/lib/libapp.a"])
        self.assertEqual(ld_flags, ["-Wl,--gc-sections", "-nostartfiles", "-Wl,--no-relax",
                                    "--specs=nano.specs", "-ufw_header", "-Wl,-wrap,snprintf",
                                    "-march=rv32imafc_xtheade"])
        self.assertEqual(libs, ["-lc", "-lm"])
        self.assertEqual(dirs, ["/sdk/macsw"])


class LinkerScriptTest(unittest.TestCase):
    def test_include_directives(self):
        text = "SECTIONS {\n  INCLUDE macsw_cache_affinity.ld.in\n}\n/* INCLUDE no */\n"
        self.assertEqual(ep.linker_includes(text), ["macsw_cache_affinity.ld.in"])


class MapIncludeDirTest(unittest.TestCase):
    ROOTS = [("/w/sdk", "sdk"), ("/w/build/p/build", "build"), ("/w/build/p", "profile")]

    def test_build_dir_wins_over_enclosing_profile_dir(self):
        self.assertEqual(ep.map_include_dir("/w/build/p/build/generated", self.ROOTS), "build/generated")

    def test_dot_suffix_normalized(self):
        self.assertEqual(ep.map_include_dir("/w/sdk/bsp/board/x/.", self.ROOTS), "sdk/bsp/board/x")
        self.assertEqual(ep.map_include_dir("/w/build/p/.", self.ROOTS), "profile")

    def test_unknown_root_rejected(self):
        with self.assertRaises(ValueError):
            ep.map_include_dir("/home/user/private", self.ROOTS)


class DepfileTest(unittest.TestCase):
    def test_headers_from_continuation_lines(self):
        text = ("x.c.obj: \\\n /s/a.c \\\n /b/generated/autoconfig.h \\\n"
                " /s/inc/./FreeRTOS.h /s/x.inc\n")
        self.assertEqual(ep.depfile_headers(text),
                         ["/b/generated/autoconfig.h", "/s/inc/FreeRTOS.h", "/s/x.inc"])


class DepfileEscapeTest(unittest.TestCase):
    def test_escaped_space_and_crlf(self):
        """路径含空格或依赖文件为 CRLF 时，头文件不能被静默漏掉。"""
        text = "x.obj: /w/my\\ sdk/a.h \\\r\n /w/b$$.h\r\n"
        self.assertEqual(ep.depfile_headers(text), ["/w/my sdk/a.h", "/w/b$.h"])


def syms(strong=(), weak=(), undef=(), local=()):
    return {"strong": set(strong), "weak": set(weak), "undef": set(undef), "local": set(local)}


class ParseNmTest(unittest.TestCase):
    def test_kinds_and_symbolless_members(self):
        text = ("/p/liba.a[x.o]: f T 0 4\n/p/liba.a[x.o]: g W 0 4\n/p/liba.a[x.o]: h U\n"
                "/p/liba.a[x.o]: v d 0 4\n/p/liba.a[x.o]: .LC0 r 0 4\n")
        self.assertEqual(ep.parse_nm(text, ["x.o", "empty.o"]),
                         {"x.o": syms(["f"], ["g"], ["h"], ["v"]), "empty.o": syms()})

    def test_duplicate_member_names_rejected(self):
        with self.assertRaises(ValueError):
            ep.parse_nm("", ["a.o", "a.o"])


class ForcedMembersTest(unittest.TestCase):
    """SDK 用 --whole-archive 时这些成员必定生效；包内按需链接时必须单独强制，否则静默换成别的实现。"""

    TOOLCHAIN = {"libc_nano.a": {"lib_a-nano-mallocr.o": syms(["_malloc_r"])}}

    def test_libc_port_overrides_newlib_for_every_program(self):
        package = {"liblibc.a": {"port_memory.c.obj": syms(["_malloc_r"], undef=["kmalloc"]),
                                 "lib_only.c.obj": syms(["sdk_only_fn"])}}
        forced = ep.forced_members(package, self.TOOLCHAIN, {"wifi": ["libphy.a"]})
        self.assertEqual(forced, {"base": [("liblibc.a", "port_memory.c.obj",
                                            "_malloc_r 也由 libc_nano.a(lib_a-nano-mallocr.o) 定义")]})

    def test_override_needing_wifi_library_goes_to_wifi_group(self):
        """监管域表依赖无线库，放进 base 会让不用 Wi-Fi 的程序也链入无线库。"""
        package = {
            "libapp.a": {"wireless_regdb.c.obj": syms(["wireless_regdb_init"], undef=["wireless_config_set_reg_db"])},
            "libphy.a": {"wireless_config.c.obj": syms(["wireless_config_set_reg_db"], ["wireless_regdb_init"])},
        }
        forced = ep.forced_members(package, {}, {"wifi": ["libphy.a"]})
        self.assertEqual(list(forced), ["wifi"])
        self.assertEqual(forced["wifi"][0][:2], ("libapp.a", "wireless_regdb.c.obj"))

    def test_local_only_member_forced_but_empty_member_not(self):
        package = {"liblhal.a": {"lhal_version.c.obj": syms(local=["version_lhal"]), "disabled.c.obj": syms()}}
        forced = ep.forced_members(package, {}, {})
        self.assertEqual(forced, {"base": [("liblhal.a", "lhal_version.c.obj", "只有局部定义")]})


class CheckReplaceableTest(unittest.TestCase):
    def test_unrelated_directory_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes.txt").write_text("keep")
            with self.assertRaises(ValueError):
                ep.check_replaceable(Path(d))

    def test_previous_export_and_empty_dir_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            ep.check_replaceable(Path(d))
            (Path(d) / "manifest.json").write_text('{"name": "x-libs", "version": "0", "files": []}')
            ep.check_replaceable(Path(d))


class MapMembersTest(unittest.TestCase):
    def test_members_with_content_and_forced_names_restored(self):
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
        log = ("bflb_fw_post_proc-ubuntu --chipname=bl616cl --imgfile=/b/x.bin --appkeys=shared "
               "--boot2file=/s/boot2.bin --ptfile=/s/pt.toml --dtsfile=/s/a.dts\n")
        self.assertEqual(ep.post_proc_files(log),
                         {"boot2": "/s/boot2.bin", "partition": "/s/pt.toml", "dts": "/s/a.dts"})


if __name__ == "__main__":
    unittest.main()
