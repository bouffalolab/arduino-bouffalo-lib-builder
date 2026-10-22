#!/usr/bin/env python3
"""export_package.py 的解析与转换测试。

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


class PostProcTest(unittest.TestCase):
    def test_boot_files_from_log(self):
        log = ("bflb_fw_post_proc-ubuntu --chipname=bl616cl --imgfile=/b/x.bin --appkeys=shared "
               "--boot2file=/s/boot2.bin --ptfile=/s/pt.toml --dtsfile=/s/a.dts\n")
        self.assertEqual(ep.post_proc_files(log),
                         {"boot2": "/s/boot2.bin", "partition": "/s/pt.toml", "dts": "/s/a.dts"})


if __name__ == "__main__":
    unittest.main()
