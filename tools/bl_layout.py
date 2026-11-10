#!/usr/bin/env python3
"""Arduino Flash layout selection, validation, image identity and upload (BL616CL)."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

SCHEMA = "arduino-bl-layout/v1"
FOTA_CHIP = b"BL616CL".ljust(16, b"\0")


def digest(path):
    """Return the SHA-256 digest of the supplied file."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_fota(image, vendor_ota, capacity):
    """Bind a post-processed raw OTA image to the selected BL616CL target."""
    raw = Path(image).read_bytes()
    ota = bytearray(Path(vendor_ota).read_bytes())
    if (len(raw) > capacity or len(raw) < 4096 or raw[:4] != b"BFNP" or
            len(ota) != len(raw) + 512 or ota[:16] != b"BL60X_OTA_Ver1.0" or
            ota[16:20] != b"RAW " or int.from_bytes(ota[20:24], "little") != len(raw) or
            ota[64:96] != hashlib.sha256(raw).digest() or ota[512:] != raw):
        raise ValueError("invalid or oversized BL616CL OTA image")
    ota[32:48] = FOTA_CHIP
    return ota


def validate(data, baseline):
    """Validate partition geometry and protected-region invariants against the baseline."""
    entries = data["pt_entry"]
    for part in entries:
        name = part["name"]
        if not name.isascii() or not 1 <= len(name) <= 7:
            raise ValueError("vendor partition names require 1-7 ASCII characters")
    parts = {p["name"]: p for p in entries}
    if len(parts) != len(entries) or len(entries) > 16:
        raise ValueError("duplicate partition names or too many entries")
    default = {p["name"]: p for p in baseline["pt_entry"]}
    if data["pt_table"] != baseline["pt_table"]:
        raise ValueError("boot partition-table locations/version must be preserved")
    for name in ("Boot2", "media", "PSM", "KEY", "DATA", "factory"):
        if parts.get(name) != default[name]:
            raise ValueError("protected partition changed: " + name)
    fw, mfg, fs = (parts[name] for name in ("FW", "mfg", "lfs"))
    for name in ("FW", "mfg", "lfs"):
        for field in ("type", "device", "header", "security"):
            if parts[name][field] != default[name][field]:
                raise ValueError("partition metadata changed: " + name + "/" + field)
    if fw["address0"] != 0x10000 or fw["size0"] != fw["size1"] or fw["size0"] < 0x10000:
        raise ValueError("FW must have equal slots, FW0 starts at 0x10000")
    if fw["address1"] != fw["address0"] + fw["size0"]:
        raise ValueError("FW slots must be contiguous")
    if (mfg["address0"], mfg["size0"], mfg["size1"]) != (fw["address1"], fw["size1"], 0):
        raise ValueError("mfg must alias FW1 exactly")
    if fs["size0"] < 8192 or fs["size1"] or fs["address0"] < fw["address1"] + fw["size1"]:
        raise ValueError("littlefs needs at least two blocks after FW1")
    ranges = []
    for part in entries:
        if part["device"] != 0:
            raise ValueError("only the board's program Flash device is supported")
        for slot in (0, 1):
            start, size = part["address" + str(slot)], part["size" + str(slot)]
            if type(start) is not int or type(size) is not int:
                raise ValueError("partition addresses/sizes must be integers")
            if start < 0 or size < 0 or start % 4096 or size % 4096 or start + size > 0x400000:
                raise ValueError("partition exceeds Flash or is not 4KiB aligned: " + part["name"])
            if not size:
                continue
            if part["name"] != "Boot2" and start < 0x10000:
                raise ValueError("partition overlaps boot2 or partition tables")
            ranges.append((part["name"], slot, start, start + size))
    for i, (name, slot, start, end) in enumerate(ranges):
        for other, other_slot, a, z in ranges[i + 1:]:
            alias = {name, other} == {"FW", "mfg"} and (start, end) == (a, z)
            if start < z and a < end and not alias:
                raise ValueError("overlapping partitions: " + name + "/" + other)
    return fw, fs


def prepare(args):
    """Prepare and validate the requested build inputs without modifying the installed package."""
    libs, build, sketch = Path(args.libs), Path(args.build), Path(args.sketch)
    baseline_path = libs / "boot" / args.default
    if args.file:
        source = Path(args.file)
        if not source.is_absolute():
            source = sketch / source
    elif args.scheme == "custom":
        source = sketch / "partitions.toml"
    else:
        source = baseline_path
    raw = source.read_bytes()  # Custom/explicit missing files never fall back.
    data = tomllib.loads(raw.decode())
    fw, fs = validate(data, tomllib.loads(baseline_path.read_text()))
    build.mkdir(parents=True, exist_ok=True)
    (build / "layout.toml").write_bytes(raw)
    linker, count = re.subn(r"(\brom\s*\(rx\)\s*:\s*ORIGIN\s*=[^\n]*?LENGTH\s*=)[^\n]+",
                            lambda m: m[1] + " " + hex(fw["size0"] - 0x2800),
                            (libs / "ld/linker.ld").read_text())
    if count != 1:
        raise ValueError("unsupported dependency linker script: expected one rom region")
    (build / "layout.ld").write_text(linker)
    manifest = {"schema": SCHEMA, "source": str(source.resolve()), "source_sha256": digest(source),
                "layout_sha256": digest(build / "layout.toml"), "linker_sha256": digest(build / "layout.ld"),
                "dependency_manifest_sha256": digest(libs / "manifest.json"),
                "boot2": args.boot2, "boot2_sha256": digest(libs / "boot" / args.boot2),
                "fw_address": fw["address0"], "fw_capacity": fw["size0"],
                "pt_address": data["pt_table"]["address0"],
                "filesystem": {"address": fs["address0"], "size": fs["size0"]}}
    (build / "layout.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Flash layout: %s sha256=%s FW=%d FS=%d" %
          (source, manifest["layout_sha256"], fw["size0"], fs["size0"]))


def artifact_paths(build, image=None):
    """Resolve the build-local firmware and layout artifact paths."""
    build = Path(build)
    if (build / "layout.json").is_file() or image is None:
        return build / "layout.json", build / "layout.toml", build / "partition.bin"
    prefix = str(image)
    return Path(prefix + ".layout.json"), Path(prefix + ".layout.toml"), Path(prefix + ".partition.bin")


def load(build, image=None):
    """Load layout sidecars and verify their schema, table and partition identities."""
    manifest, table, partition = artifact_paths(build, image)
    m = json.loads(manifest.read_text())
    if m["schema"] != SCHEMA or digest(table) != m["layout_sha256"]:
        raise ValueError("layout snapshot missing or identity mismatch")
    if image is not None:
        if digest(image) != m["image_sha256"] or digest(partition) != m["partition_sha256"]:
            raise ValueError("compiled image/partition identity mismatch; rebuild before upload")
        if Path(image).stat().st_size > m["fw_capacity"]:
            raise ValueError("image exceeds firmware partition")
    return m


def finalize(args):
    """Post-process the linked image and freeze its validated layout and dependency identity."""
    build, libs, tools = Path(args.build), Path(args.libs), Path(args.tools)
    m = load(build)
    if digest(build / "layout.ld") != m["linker_sha256"]:
        raise ValueError("generated linker identity mismatch")
    if digest(libs / "manifest.json") != m["dependency_manifest_sha256"]:
        raise ValueError("dependency changed during build")
    image = build / (args.project + ".bin")
    (build / "partition.bin").unlink(missing_ok=True)
    subprocess.run([str(tools / "bflb_fw_post_proc/bflb_fw_post_proc-ubuntu"), "--chipname=bl616cl",
                    "--imgfile=" + str(image), "--appkeys=shared", "--ptfile=" + str(build / "layout.toml"),
                    "--dtsfile=" + str(libs / "boot" / args.dts)], check=True)
    if image.stat().st_size > m["fw_capacity"]:
        raise ValueError("post-processed image exceeds firmware partition")
    Path(str(image) + ".blfota").write_bytes(make_fota(image, str(image) + ".ota", m["fw_capacity"]))
    m.update(image_sha256=digest(image), partition_sha256=digest(build / "partition.bin"))
    (build / "layout.json").write_text(json.dumps(m, indent=2) + "\n")
    # Arduino CLI --output-dir copies the image and its .bin.* sidecars.
    for name, suffix in (("layout.json", ".layout.json"), ("layout.toml", ".layout.toml"), ("partition.bin", ".partition.bin")):
        shutil.copy2(build / name, Path(str(image) + suffix))


def report_size(args):
    """Print firmware size and the selected partition capacity for Arduino size reporting."""
    build = Path(args.build)
    m = load(build, build / (args.project + ".bin"))
    text = subprocess.check_output([args.size_tool, "-A", str(build / (args.project + ".elf"))], text=True)
    flash_names = {".fw_header", ".init", ".rftlv.tool", ".ver", ".init_code", "rom_text", "rom_rodata",
                   ".last_section", "tcm_text", "tcm_rodata", "tcm_data", "ram_data", "ram_nocache_data"}
    ram_names = {"tcm_text", "tcm_rodata", "tcm_data", "ram_data", "ram_bss", "ram_noinit",
                 "ram_nocache_data", "ram_nocache_noinit"}
    flash = ram = 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            if fields[0] in flash_names:
                flash += int(fields[1])
            if fields[0] in ram_names:
                ram += int(fields[1])
    bad = flash > m["fw_capacity"] or ram > args.ram
    print(json.dumps({"output": "Flash %d/%d bytes; RAM %d/%d bytes; FS %d bytes" %
                     (flash, m["fw_capacity"], ram, args.ram, m["filesystem"]["size"]),
                      "severity": "error" if bad else "info", "error": "Sketch exceeds memory limits" if bad else "",
                      "sections": [{"name": "Flash", "size": flash, "max_size": m["fw_capacity"]},
                                   {"name": "RAM", "size": ram, "max_size": args.ram}]}))


def upload(args):
    """Validate finalized artifact identity, then invoke the build-local flash runner."""
    build, libs, tools = Path(args.build), Path(args.libs), Path(args.tools)
    image = build / (args.project + ".bin")
    m = load(build, image)
    if digest(libs / "boot" / m["boot2"]) != m["boot2_sha256"]:
        raise ValueError("boot2 identity mismatch")
    _, table, partition = artifact_paths(build, image)
    backup_pt = tomllib.loads(table.read_text())["pt_table"]["address1"]
    # BLFlashCommand writes its own configuration/log directories. Keep the installed tools read-only.
    runner = build / "flash-runner"
    shutil.copytree(tools / "bouffalo_flash_cube", runner, dirs_exist_ok=True)
    subprocess.run([str(runner / "BLFlashCommand-ubuntu"), "--interface=uart", "--chipname=bl616cl",
                    "--port=" + args.port, "--baudrate=" + args.speed, "--warm_reset", "write_flash_files",
                    "0x0", str(libs / "boot" / m["boot2"]), hex(m["pt_address"]), str(partition),
                    hex(backup_pt), str(partition),
                    hex(m["fw_address"]), str(image)], check=True)


def export_images(args):
    """Export finalized firmware images with their matching layout metadata."""
    build = Path(args.build)
    image = build / (args.project + ".bin")
    load(build, image)
    out = Path(args.destination)
    out.mkdir(parents=True, exist_ok=True)
    manifest, table, partition = artifact_paths(build, image)
    for source, name in ((image, args.project + ".bin"), (partition, "partition.bin"),
                         (manifest, "layout.json"), (table, "layout.toml")):
        dst = out / name
        if source.resolve() != dst.resolve():
            shutil.copy2(source, dst)
    fota = Path(str(image) + ".blfota")
    if fota.is_file():
        shutil.copy2(fota, out / (args.project + ".bin.blfota"))
    print("Flash bundle: " + str(out))


def main(argv=None):
    """Parse options and run the bl layout validation or packaging command."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--sketch", required=True)
    p.add_argument("--scheme", choices=("default", "custom"), required=True)
    p.add_argument("--file", default="")
    p.add_argument("--default", required=True)
    p.add_argument("--boot2", required=True)
    p.set_defaults(action=prepare)
    p = commands.add_parser("finalize")
    p.add_argument("--dts", required=True)
    p.set_defaults(action=finalize)
    p = commands.add_parser("size")
    p.add_argument("--size-tool", required=True)
    p.add_argument("--ram", type=int, required=True)
    p.set_defaults(action=report_size)
    p = commands.add_parser("upload")
    p.add_argument("--port", required=True)
    p.add_argument("--speed", required=True)
    p.set_defaults(action=upload)
    p = commands.add_parser("export")
    p.add_argument("--destination", required=True)
    p.set_defaults(action=export_images)
    for p in commands.choices.values():
        p.add_argument("--build", required=True)
    for name in ("prepare", "finalize", "upload"):
        commands.choices[name].add_argument("--libs", required=True)
    for name in ("finalize", "upload"):
        commands.choices[name].add_argument("--tools", required=True)
    for name in ("finalize", "size", "upload", "export"):
        commands.choices[name].add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        args.action(args)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print("bl-layout: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
