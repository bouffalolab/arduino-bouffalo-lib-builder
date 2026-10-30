#!/usr/bin/env python3
"""Test prepared SDK trees. Shared hardlinks must be detached before patching or the clean

checkout would silently change and violate pinned provenance. Mismatched/replaced
patches or interrupted preparation must not leave partial outputs that a build could
consume.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_sdk_tree as pst  # noqa: E402

ORIGINAL = "int stall(void)\n{\n    set_stl();\n    return 0;\n}\n"
PATCH = """fix: complete the transfer when stalling

--- a/drivers/usb.c
+++ b/drivers/usb.c
@@ -1,5 +1,6 @@
 int stall(void)
 {
     set_stl();
+    set_done();
     return 0;
 }
"""


def git(root, *args):
    """Run Git in the explicitly selected source repository."""
    cmd = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


class PrepareTest(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture inputs for each software test."""
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.sdk = base / "sdk"
        (self.sdk / "drivers").mkdir(parents=True)
        (self.sdk / "drivers" / "usb.c").write_text(ORIGINAL)
        (self.sdk / "drivers" / "other.c").write_text("int other;\n")
        os.symlink("usb.c", self.sdk / "drivers" / "link.c")
        git(self.sdk, "init", "-q")
        git(self.sdk, "add", "-A")
        git(self.sdk, "commit", "-qm", "sdk")
        self.commit = git(self.sdk, "rev-parse", "HEAD")
        self.builder = base / "builder"
        (self.builder / "patches").mkdir(parents=True)
        self.patch = self.builder / "patches" / "usb.patch"
        self.patch.write_text(PATCH)
        self.out = base / "build" / "tree"

    def tearDown(self):
        """Release this software test's temporary resources."""
        self.tmp.cleanup()

    def write_sources(self, patches=None, commit=None):
        """Write pinned source and patch metadata for the prepared-tree test fixture."""
        if patches is None:
            patches = [{"file": "patches/usb.patch", "sha256": hashlib.sha256(PATCH.encode()).hexdigest()}]
        sources = self.builder / "sources.json"
        sources.write_text(json.dumps({"sdk": {"commit": commit or self.commit, "patches": patches}}))
        return sources

    def prepare(self, **kw):
        """Prepare and validate the requested build inputs without modifying the installed package."""
        return pst.prepare(self.sdk, self.write_sources(**kw), self.out)

    def test_patch_changes_tree_but_not_checkout(self):
        """Verify patch changes tree but not checkout."""
        self.prepare()
        self.assertIn("set_done();", (self.out / "drivers" / "usb.c").read_text())
        self.assertEqual((self.sdk / "drivers" / "usb.c").read_text(), ORIGINAL)
        self.assertEqual(git(self.sdk, "status", "--porcelain", "--ignored"), "")
        self.assertNotEqual((self.out / "drivers" / "usb.c").stat().st_ino,
                            (self.sdk / "drivers" / "usb.c").stat().st_ino)

    def test_unpatched_files_are_hard_links_and_symlinks_kept(self):
        """Verify unpatched files are hard links and symlinks kept."""
        self.prepare()
        self.assertEqual((self.out / "drivers" / "other.c").stat().st_ino,
                         (self.sdk / "drivers" / "other.c").stat().st_ino)
        self.assertEqual(os.readlink(self.out / "drivers" / "link.c"), "usb.c")
        self.assertFalse((self.out / ".git").exists())

    def test_marker_records_commit_and_patches(self):
        """Verify marker records commit and patches."""
        self.prepare()
        marker = pst.read_marker(self.out)
        self.assertEqual(marker["commit"], self.commit)
        self.assertEqual([p["file"] for p in marker["patches"]], ["patches/usb.patch"])

    def test_without_patches_tree_matches_checkout(self):
        """Verify without patches tree matches checkout."""
        self.prepare(patches=[])
        self.assertEqual((self.out / "drivers" / "usb.c").read_text(), ORIGINAL)
        self.assertEqual(pst.read_marker(self.out)["patches"], [])

    def test_sha256_mismatch_keeps_previous_tree(self):
        """Verify sha256 mismatch keeps previous tree."""
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare(patches=[{"file": "patches/usb.patch", "sha256": "0" * 64}])
        self.assertIn("set_done();", (self.out / "drivers" / "usb.c").read_text())
        self.assertEqual([p.name for p in self.out.parent.iterdir()], ["tree"])

    def test_patch_that_does_not_apply_fails_cleanly(self):
        """Verify patch that does not apply fails cleanly."""
        (self.sdk / "drivers" / "usb.c").write_text(ORIGINAL.replace("set_stl", "other"))
        git(self.sdk, "commit", "-qam", "changed")
        self.commit = git(self.sdk, "rev-parse", "HEAD")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.out.parent.exists() and any(self.out.parent.iterdir()))
        self.assertEqual(git(self.sdk, "status", "--porcelain", "--ignored"), "")

    def test_dirty_or_unpinned_checkout_rejected(self):
        """Verify dirty or unpinned checkout rejected."""
        with self.assertRaises(ValueError):
            self.prepare(commit="0" * 40)
        (self.sdk / "untracked.txt").write_text("x")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_refuses_to_replace_unrelated_directory(self):
        """Verify refuses to replace unrelated directory."""
        self.out.mkdir(parents=True)
        (self.out / "keep.txt").write_text("user data")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.out / "keep.txt").read_text(), "user data")

    def test_check_tree_rejects_other_commit_or_patch_list(self):
        """Verify check tree rejects other commit or patch list."""
        sources = self.write_sources()
        pst.prepare(self.sdk, sources, self.out)
        pinned = json.loads(sources.read_text())["sdk"]
        pst.check_tree(self.out, pinned)
        with self.assertRaises(ValueError):
            pst.check_tree(self.out, {**pinned, "patches": []})
        with self.assertRaises(ValueError):
            pst.check_tree(self.out, {**pinned, "commit": "0" * 40})
        with self.assertRaises(ValueError):
            pst.check_tree(self.sdk, pinned)

    def test_patch_targets_strip_one_level(self):
        """Verify patch targets strip one level."""
        self.assertEqual(pst.patch_targets(PATCH), ["drivers/usb.c"])
        with self.assertRaises(ValueError):
            pst.patch_targets("no headers here\n")


if __name__ == "__main__":
    unittest.main()
