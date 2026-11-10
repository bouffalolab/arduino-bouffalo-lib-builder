#!/usr/bin/env python3
"""Software auxiliary checks: layout contracts and fake uploader argument assembly, no hardware."""
import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import bl_layout as layout

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "profiles/bl616cl/default/partition_cfg_4M_lfs_v1.toml"


class LayoutTest(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture inputs for each software test."""
        self.tmp = tempfile.TemporaryDirectory(prefix="layout checks ")
        self.root = Path(self.tmp.name)
        self.libs = self.root / "installed libs"
        (self.libs / "boot").mkdir(parents=True)
        (self.libs / "ld").mkdir()
        (self.libs / "boot/default.toml").write_bytes(DEFAULT.read_bytes())
        (self.libs / "boot/boot2.bin").write_bytes(b"boot")
        (self.libs / "ld/linker.ld").write_text("rom (rx) : ORIGIN = 0x80000000 + 0x1800, LENGTH = 0x400000\n")
        (self.libs / "manifest.json").write_text("{}")
        self.sketch = self.root / "project with spaces"
        self.sketch.mkdir()
        self.build = self.root / "build"
        self.args = argparse.Namespace(libs=str(self.libs), build=str(self.build), sketch=str(self.sketch),
                                       default="default.toml", boot2="boot2.bin", scheme="default", file="")
        self.base = layout.tomllib.loads(DEFAULT.read_text())

    def tearDown(self):
        """Release this software test's temporary resources."""
        self.tmp.cleanup()

    def prepare(self):
        """Prepare and validate the requested build inputs without modifying the installed package."""
        with contextlib.redirect_stdout(io.StringIO()):
            layout.prepare(self.args)
        return layout.load(self.build)

    def custom(self):
        """Create a modified partition-table fixture for validation tests."""
        chunks = DEFAULT.read_text().split("[[pt_entry]]")
        for i in range(1, len(chunks)):
            c = chunks[i]
            if 'name = "FW"' in c:
                c = c.replace("size0 = 0x180000", "size0 = 0x100000").replace("size1 = 0x180000", "size1 = 0x100000").replace("address1 = 0x190000", "address1 = 0x110000")
            if 'name = "mfg"' in c:
                c = c.replace("address0 = 0x190000", "address0 = 0x110000").replace("size0 = 0x180000", "size0 = 0x100000")
            if 'name = "lfs"' in c:
                c = c.replace("address0 = 0x310000", "address0 = 0x210000").replace("size0 = 0x68000", "size0 = 0x168000")
            chunks[i] = c
        return "[[pt_entry]]".join(chunks)

    def test_default_geometry_and_link_bound(self):
        """Verify default geometry and link bound."""
        m = self.prepare()
        self.assertEqual(m["filesystem"], {"address": 0x310000, "size": 416 * 1024})
        self.assertIn("0x17d800", (self.build / "layout.ld").read_text())

    def test_fota_binds_chip_and_rejects_corrupt_images(self):
        """A generated image must match the processed raw firmware and its digest."""
        raw = b"BFNP" + bytes(4092)
        image = self.root / "image.bin"
        ota = self.root / "image.bin.ota"
        image.write_bytes(raw)
        header = bytearray(512)
        header[:16] = b"BL60X_OTA_Ver1.0"
        header[16:20] = b"RAW "
        header[20:24] = len(raw).to_bytes(4, "little")
        header[64:96] = hashlib.sha256(raw).digest()
        ota.write_bytes(header + raw)
        result = layout.make_fota(image, ota, len(raw))
        self.assertEqual(result[32:48], layout.FOTA_CHIP)
        self.assertEqual(result[512:], raw)
        with self.assertRaises(ValueError):
            layout.make_fota(image, ota, len(raw) - 1)
        header[64] ^= 1
        ota.write_bytes(header + raw)
        with self.assertRaises(ValueError):
            layout.make_fota(image, ota, len(raw))

    def test_custom_changes_capacity_without_rebuilding_libs(self):
        """Verify custom changes capacity without rebuilding libs."""
        self.args.scheme = "custom"
        (self.sketch / "partitions.toml").write_text(self.custom())
        m = self.prepare()
        self.assertEqual(m["fw_capacity"], 0x100000)
        self.assertEqual(m["filesystem"]["address"], 0x210000)
        self.assertIn("0xfd800", (self.build / "layout.ld").read_text())

    def test_missing_custom_does_not_fall_back(self):
        """Verify missing custom does not fall back."""
        self.args.scheme = "custom"
        with self.assertRaises(FileNotFoundError): self.prepare()

    def test_explicit_relative_file_takes_priority(self):
        """Verify explicit relative file takes priority."""
        self.args.scheme = "custom"
        self.args.file = "outside menu.toml"
        (self.sketch / self.args.file).write_text(self.custom())
        self.assertEqual(self.prepare()["fw_capacity"], 0x100000)

    def test_invalid_custom_table_fails(self):
        """Verify invalid custom table fails."""
        self.args.file = "broken.toml"
        (self.sketch / self.args.file).write_text("invalid toml")
        with self.assertRaises(ValueError): self.prepare()

    def test_protected_regions_cannot_be_changed(self):
        """Verify protected regions cannot be changed."""
        for name in ("Boot2", "media", "factory"):
            data = layout.tomllib.loads(DEFAULT.read_text())
            next(p for p in data["pt_entry"] if p["name"] == name)["size0"] += 4096
            with self.assertRaises(ValueError): layout.validate(data, self.base)

    def test_mfg_must_exactly_alias_fw1(self):
        """Verify mfg must exactly alias fw1."""
        data = layout.tomllib.loads(DEFAULT.read_text())
        next(p for p in data["pt_entry"] if p["name"] == "mfg")["address0"] += 4096
        with self.assertRaises(ValueError): layout.validate(data, self.base)

    def test_overlapping_or_misaligned_fs_is_rejected(self):
        """Verify overlapping or misaligned fs is rejected."""
        for field, value in (("address0", 0x300000), ("size0", 0x69000), ("size0", 8193)):
            data = layout.tomllib.loads(DEFAULT.read_text())
            next(p for p in data["pt_entry"] if p["name"] == "lfs")[field] = value
            with self.assertRaises(ValueError): layout.validate(data, self.base)

    def test_corrupt_layout_snapshot_is_rejected(self):
        """Verify corrupt layout snapshot is rejected."""
        self.prepare()
        (self.build / "layout.toml").write_text(self.custom())
        with self.assertRaises(ValueError): layout.load(self.build)

    def images(self):
        """Create matching image and metadata fixtures for artifact-validation tests."""
        m = self.prepare()
        (self.build / "Test.ino.bin").write_bytes(b"image")
        (self.build / "partition.bin").write_bytes(b"partition")
        m.update(image_sha256=layout.digest(self.build / "Test.ino.bin"), partition_sha256=layout.digest(self.build / "partition.bin"))
        (self.build / "layout.json").write_text(json.dumps(m))

    def test_export_is_independent_of_original_custom_file(self):
        """Verify export is independent of original custom file."""
        self.args.file = "customer.toml"
        source = self.sketch / self.args.file
        source.write_text(self.custom())
        self.images()
        source.unlink()
        self.args.project = "Test.ino"
        self.args.destination = str(self.root / "export")
        with contextlib.redirect_stdout(io.StringIO()): layout.export_images(self.args)
        layout.load(self.args.destination, Path(self.args.destination) / "Test.ino.bin")

    def test_postprocessor_success_without_new_partition_cannot_reuse_stale_table(self):
        """Verify postprocessor success without new partition cannot reuse stale table."""
        self.images()
        self.args.project = "Test.ino"
        self.args.tools = str(self.root / "tools")
        self.args.dts = "unused.dts"
        # Reproduce the observed vendor tool: exit 0 but no partition output.
        with mock.patch.object(layout.subprocess, "run"):
            with self.assertRaises(FileNotFoundError):
                layout.finalize(self.args)

    def test_cli_output_directory_prefixed_artifacts_are_self_contained(self):
        """Verify cli output directory prefixed artifacts are self contained."""
        self.images()
        out = self.root / "CLI output directory"
        out.mkdir()
        (out / "Test.ino.bin").write_bytes((self.build / "Test.ino.bin").read_bytes())
        for src, suffix in (("layout.json", ".layout.json"), ("layout.toml", ".layout.toml"), ("partition.bin", ".partition.bin")):
            (out / ("Test.ino.bin" + suffix)).write_bytes((self.build / src).read_bytes())
        self.assertEqual(layout.load(out, out / "Test.ino.bin")["fw_capacity"], 0x180000)

    def test_changed_image_is_rejected_before_uploader(self):
        """Verify changed image is rejected before uploader."""
        self.images()
        (self.build / "Test.ino.bin").write_bytes(b"different")
        with self.assertRaises(ValueError): layout.load(self.build, self.build / "Test.ino.bin")

    def test_upload_uses_recorded_layout_and_excludes_fs(self):
        """Verify upload uses recorded layout and excludes fs."""
        self.images()
        tools = self.root / "installed tools"
        (tools / "bouffalo_flash_cube").mkdir(parents=True)
        (tools / "bouffalo_flash_cube/BLFlashCommand-ubuntu").write_bytes(b"fake executable fixture")
        self.args.tools, self.args.project, self.args.port, self.args.speed = str(tools), "Test.ino", "FAKE", "2000000"
        with mock.patch.object(layout.subprocess, "run") as run:
            layout.upload(self.args)
        cmd = run.call_args.args[0]
        # A stale secondary table can otherwise select an old OTA slot after upload.
        self.assertEqual(cmd[cmd.index("write_flash_files") + 1::2], ["0x0", "0xe000", "0xf000", "0x10000"])
        self.assertIn("0xe000", cmd)
        self.assertIn("0x10000", cmd)
        self.assertNotIn("0x310000", cmd)
        self.assertTrue(str(self.build / "flash-runner") in cmd[0])


if __name__ == "__main__": unittest.main()
