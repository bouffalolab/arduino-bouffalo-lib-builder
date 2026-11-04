#!/usr/bin/env python3
"""Test vendor-tool export. Arduino executes these tools directly and they resolve chip

configuration beside themselves; missing files, lost executable modes or unpinned SDK
inputs would otherwise fail only during upload.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_flash_tools as eft  # noqa: E402


def make_sdk(root):
    """Create and commit a minimal SDK fixture with fixed tools, two chip configurations

    and LICENSE.
    """
    tools = root / eft.TOOLS_DIR
    for rel in eft.FILES:
        (tools / rel).parent.mkdir(parents=True, exist_ok=True)
        (tools / rel).write_text(rel)
        if rel.endswith("-ubuntu"):
            os.chmod(tools / rel, 0o755)
    for chip in ("bl616cl", "bl602"):
        cfg = tools / "bouffalo_flash_cube" / "chips" / chip / "eflash_loader" / "eflash_loader_cfg.conf"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(chip)
    (root / "LICENSE").write_text("Apache-2.0")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git[:3] + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "sdk"], check=True)
    return subprocess.run(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


class ToolFilesTest(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture inputs for each software test."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_sdk(self.root)
        self.tools = self.root / eft.TOOLS_DIR

    def tearDown(self):
        """Release this software test's temporary resources."""
        self.tmp.cleanup()

    def test_only_selected_chip_config_is_exported(self):
        """Verify only selected chip config is exported."""
        rels = eft.tool_files(self.tools, ["bl616cl"])
        self.assertIn("bouffalo_flash_cube/chips/bl616cl/eflash_loader/eflash_loader_cfg.conf", rels)
        self.assertFalse(any("/chips/bl602/" in r for r in rels))
        self.assertTrue(set(eft.FILES) <= set(rels))

    def test_unknown_chip_fails(self):
        """Verify unknown chip fails."""
        with self.assertRaises(ValueError):
            eft.tool_files(self.tools, ["bl999"])

    def test_missing_program_fails(self):
        """Verify missing program fails."""
        (self.tools / "bouffalo_flash_cube" / "BLFlashCommand-ubuntu").unlink()
        with self.assertRaises(ValueError):
            eft.tool_files(self.tools, ["bl616cl"])


class ExportTest(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture inputs for each software test."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()
        self.commit = make_sdk(self.sdk)

    def tearDown(self):
        """Release this software test's temporary resources."""
        self.tmp.cleanup()

    def args(self):
        """Construct the command-line argument fixture for the SDK checkout."""
        sources = self.root / "sources.json"
        sources.write_text(json.dumps({"sdk": {"repo": "r", "ref": "v1"}}))
        return Namespace(sdk=str(self.sdk), sources=str(sources), chip=["bl616cl"], version="0.1.0",
                         out=str(self.root / "out"))

    def test_export_keeps_executables_and_lists_every_file(self):
        """Verify export keeps executables and lists every file."""
        manifest = eft.export(self.args(), eft.write_tools)
        out = self.root / "out"
        for prog in ("bflb_fw_post_proc/bflb_fw_post_proc-ubuntu", "bouffalo_flash_cube/BLFlashCommand-ubuntu"):
            self.assertTrue(os.access(out / prog, os.X_OK), prog)
        on_disk = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()} - {"manifest.json"}
        self.assertEqual({f["path"] for f in manifest["files"]}, on_disk)
        # sources.json names only a ref; the manifest records the commit the tools came from.
        self.assertEqual(manifest["sdk"], {"repo": "r", "ref": "v1", "commit": self.commit})

    def test_dirty_sdk_is_rejected(self):
        """Tools from a modified checkout would not match the recorded commit."""
        (self.sdk / "untracked.txt").write_text("x")
        with self.assertRaises(ValueError):
            eft.export(self.args(), eft.write_tools)
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
