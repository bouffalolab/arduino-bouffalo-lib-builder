#!/usr/bin/env python3
"""Check dependency linking from GNU ld maps. Commands: members <map> lists non-debug

archive contributors; absent <manifest> <group> <map> rejects a link group; parity
<manifest> <reference-map> <map> requires every contributing package member from the SDK
whole-archive reference; sysinit <manifest> <map> [group...] requires the base and listed
groups' kept SYSINIT records and rejects records kept only by other groups. Compare package archives only: duplicate
toolchain functions such as nan may resolve by load order. Restore libforce member names
from original-archive@member. Parse from the first LOAD line without relying on linker
language.
"""

import fnmatch
import json
import os
import re
import sys

IGNORED_SECTIONS = (".debug", ".comment", ".riscv.attributes", ".note", ".gnu.attributes")
INPUT_LINE = re.compile(r"^ (\S+)?\s+0x[0-9a-f]+\s+0x([0-9a-f]+)\s+(\S+\.a)\(([^)]+)\)$")
NAME_LINE = re.compile(r"^ (\S+)$")
SYSINIT_LINE = re.compile(r"^ \.sysinit\.[0-9a-f]{2}\.[0-9a-f]{2}\.(\S+)")


def map_members(text):
    """Return archive/member pairs contributing nonzero, non-debug input sections to the

    memory map.
    """
    start = text.find("\nLOAD ")
    if start < 0:
        raise ValueError("not a GNU ld map file: no LOAD line")
    members = set()
    section = None
    for line in text[start:].splitlines():
        m = NAME_LINE.match(line)
        if m:
            section = m.group(1)  # For long section names, address and size appear on the following line.
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


def map_sysinit(text):
    """Return SYSINIT record names placed in the memory map; records are KEEP sections."""
    start = text.find("\nLOAD ")
    if start < 0:
        raise ValueError("not a GNU ld map file: no LOAD line")
    return {m.group(1) for m in map(SYSINIT_LINE.match, text[start:].splitlines()) if m}


def sysinit_problems(manifest, records, groups):
    """List missing kept records and records kept only by groups not linked."""
    keep = manifest["link"].get("sysinit", {}).get("keep", {})
    want = {e["record"] for g in ["base"] + list(groups) for e in keep.get(g, [])}
    other = {e["record"] for g, entries in keep.items() for e in entries} - want
    return (["missing " + r for r in sorted(want - records)] +
            ["unexpected " + r for r in sorted(other & records)])


def read_members(path):
    """Read the expected archive-member set used by link verification."""
    with open(path, errors="replace") as f:
        return map_members(f.read())


def main(argv):
    """Parse options and run the check link validation or packaging command."""
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
    if len(argv) >= 3 and argv[0] == "sysinit":
        with open(argv[1]) as f:
            manifest = json.load(f)
        with open(argv[2], errors="replace") as f:
            problems = sysinit_problems(manifest, map_sysinit(f.read()), argv[3:])
        for p in problems:
            print("  " + p)
        return 1 if problems else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
