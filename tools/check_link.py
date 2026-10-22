#!/usr/bin/env python3
"""用 GNU ld map 文件检查底层包的链接结果。

  check_link.py members <map>                   列出贡献了非调试段内容的归档成员
  check_link.py absent <manifest> <组> <map>     map 中不得有该链接组归档的成员
  check_link.py parity <manifest> <参考 map> <map>
                                                参考 map 中有内容的包内归档成员，map 中也必须有

参考 map 来自 SDK 原生的 --whole-archive 链接。parity 只比较包内归档：工具链库中同一函数可能
在多个库里各有一份（如 nan 同在 libc_nano 和 libm_nano），取哪一份只随加载顺序变化。
libforce_<组>.a 的成员名是“原归档名@原成员名”，比较前还原为原归档成员。不依赖 ld 输出语言：内存映射部分从第一行 LOAD 开始。
"""

import fnmatch
import json
import os
import re
import sys

IGNORED_SECTIONS = (".debug", ".comment", ".riscv.attributes", ".note", ".gnu.attributes")
INPUT_LINE = re.compile(r"^ (\S+)?\s+0x[0-9a-f]+\s+0x([0-9a-f]+)\s+(\S+\.a)\(([^)]+)\)$")
NAME_LINE = re.compile(r"^ (\S+)$")


def map_members(text):
    """返回 {(归档名, 成员名)}：在内存映射部分贡献了非零、非调试输入段的成员。"""
    start = text.find("\nLOAD ")
    if start < 0:
        raise ValueError("not a GNU ld map file: no LOAD line")
    members = set()
    section = None
    for line in text[start:].splitlines():
        m = NAME_LINE.match(line)
        if m:
            section = m.group(1)  # 段名过长时，地址和大小在下一行
            continue
        m = INPUT_LINE.match(line)
        name, section = (m.group(1) or section, None) if m else (None, None)
        if not m or name is None or name.startswith(IGNORED_SECTIONS) or int(m.group(2), 16) == 0:
            continue
        archive, member = os.path.basename(m.group(3)), m.group(4)
        if archive.startswith("libforce_") and "@" in member:
            archive, member = member.split("@", 1)
        members.add((archive, member))
    return members


def read_members(path):
    with open(path, errors="replace") as f:
        return map_members(f.read())


def main(argv):
    if len(argv) == 2 and argv[0] == "members":
        for archive, member in sorted(read_members(argv[1])):
            print("%s(%s)" % (archive, member))
        return 0
    if len(argv) == 4 and argv[0] == "absent":
        with open(argv[1]) as f:
            patterns = json.load(f)["link"]["groups"][argv[2]]["archives"]
        bad = sorted(m for m in read_members(argv[3]) if any(fnmatch.fnmatch(m[0], p) for p in patterns))
        for archive, member in bad:
            print("  %s(%s)" % (archive, member))
        return 1 if bad else 0
    if len(argv) == 4 and argv[0] == "parity":
        with open(argv[1]) as f:
            package = {os.path.basename(e["path"]) for e in json.load(f)["files"] if e["path"].startswith("lib/")}
        missing = sorted(m for m in read_members(argv[2]) - read_members(argv[3]) if m[0] in package)
        for archive, member in missing:
            print("  missing %s(%s)" % (archive, member))
        return 1 if missing else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
