import json
import tempfile
import unittest
from pathlib import Path

import sdk_sync

LS_REMOTE = (
    "aaa\trefs/tags/v2.3.9\n"
    "bbb\trefs/tags/v2.3.9^{}\n"
    "ccc\trefs/tags/v2.3.10\n"
    "ddd\trefs/tags/v2.3.37-history-base\n"
    "eee\trefs/tags/v2.3.38\n"
    "fff\trefs/tags/v2.3.38^{}\n"
    "ggg\trefs/tags/compact-old\n"
)


class SdkSyncTest(unittest.TestCase):
    def test_only_release_tags_count(self):
        """Helper tags such as v2.3.37-history-base are not SDK releases and must never be proposed."""
        self.assertEqual(sdk_sync.release_tags(LS_REMOTE), {"v2.3.9", "v2.3.10", "v2.3.38"})

    def test_newest_by_number_not_text(self):
        """Text order would rank v2.3.9 above v2.3.10 and propose a downgrade."""
        self.assertEqual(sdk_sync.newer_release("v2.3.9", {"v2.3.9", "v2.3.10"}), "v2.3.10")

    def test_no_update_when_current_is_newest(self):
        """Pinned to the newest release: the scheduled run must not open a PR."""
        self.assertIsNone(sdk_sync.newer_release("v2.3.38", sdk_sync.release_tags(LS_REMOTE)))

    def test_branch_ref_left_alone(self):
        """A branch ref already follows upstream; switching it to a tag would change the maintainer's choice."""
        self.assertIsNone(sdk_sync.newer_release("master", {"v9.9.9"}))

    def test_set_ref_changes_only_the_ref(self):
        """Patch hashes and toolchain pins stay as reviewed; the sync PR changes only the SDK release."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sources.json"
            data = {"sdk": {"repo": "r", "ref": "v2.3.38", "patches": [{"file": "x", "sha256": "0"}]},
                    "toolchain": {"sha256": "1"}}
            p.write_text(json.dumps(data, indent=2) + "\n")
            sdk_sync.set_ref(p, "v2.3.39")
            data["sdk"]["ref"] = "v2.3.39"
            self.assertEqual(json.loads(p.read_text()), data)
            with self.assertRaises(ValueError):
                sdk_sync.set_ref(p, "master")


if __name__ == "__main__":
    unittest.main()
