#!/usr/bin/env python3
"""Host-only release tests with small fixture archives and a real loopback server."""

import functools
import http.server
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import release


class ReleaseTests(unittest.TestCase):
    """Check approval, asset ordering, immutable versions and index recovery."""

    def setUp(self):
        """Create isolated fixture inputs and serve the target over real HTTP."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candidate = self.root / "candidate"
        self.target = self.root / "target"
        self.candidate.mkdir()
        self.target.mkdir()
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(self.target))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.approval = self.root / "approval.json"
        self.make_candidate("0.1.0")

    def make_candidate(self, version, payload=b"fixture, not firmware"):
        """Create and approve a minimal package-index fixture with exact identities."""
        name = "platform-%s.tar.gz" % version
        archive = self.candidate / name
        archive.write_bytes(payload)
        index = {"packages": [{"name": "bouffalolab", "tools": [], "platforms": [{
            "architecture": "bl", "version": version, "url": self.url + "/" + name,
            "archiveFileName": name, "checksum": "SHA-256:" + release.digest(archive),
            "size": str(len(payload)), "toolsDependencies": []}]}]}
        release.save(self.candidate / release.INDEX, index)
        release.save(self.candidate / "provenance.json", {
            "commits": {n: "1" * 40 for n in ("platform", "builder", "bridge")},
            "sdk": "fixture", "compatibility": "host test only"})
        identity = release.freeze(self.candidate)
        release.save(self.approval, {"candidate_sha256": identity, "approved_by": "test fixture",
            "license_approved": True, "channel": "isolated",
            "acceptance": {g: "fixture evidence only" for g in release.GATES}})

    def promote(self):
        """Execute the real promotion code against the loopback target."""
        return release.promote(self.candidate, self.approval, self.target, self.url)

    def test_positive_repeat_upgrade_restore(self):
        """Repetition stays identical; adding a version retains the old install entry."""
        initial = self.promote()
        self.assertEqual(initial, self.promote())
        self.make_candidate("0.2.0")
        self.promote()
        index = release.load(self.target / release.INDEX)
        self.assertEqual(2, len(index["packages"][0]["platforms"]))
        snapshot = self.target / "history" / (initial + ".json")
        self.assertEqual(initial, release.restore(snapshot, self.target))

    def test_missing_license_or_gate_or_wrong_candidate(self):
        """An unapproved candidate must leave the target index absent."""
        original = release.load(self.approval)
        for key, value in (("license_approved", False), ("acceptance", {}), ("candidate_sha256", "0" * 64)):
            approval = dict(original)
            approval[key] = value
            release.save(self.approval, approval)
            with self.assertRaises(ValueError):
                self.promote()
            self.assertFalse((self.target / release.INDEX).exists())

    def test_corrupt_or_missing_asset(self):
        """Changed or missing frozen bytes cannot enter the served index."""
        path = self.candidate / "platform-0.1.0.tar.gz"
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.promote()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.promote()
        self.assertFalse((self.target / release.INDEX).exists())

    def test_same_version_is_immutable(self):
        """A new approval cannot overwrite an already published version."""
        initial = self.promote()
        self.make_candidate("0.1.0", b"different content")
        with self.assertRaises(ValueError):
            self.promote()
        self.assertEqual(initial, release.digest(self.target / release.INDEX))

    def test_failed_download_keeps_index_then_retry(self):
        """Failure after staging assets preserves the previous installable index."""
        initial = self.promote()
        self.make_candidate("0.2.0")
        with patch("release.verify_downloads", side_effect=OSError("injected HTTP outage")):
            with self.assertRaises(OSError):
                self.promote()
        self.assertEqual(initial, release.digest(self.target / release.INDEX))
        self.promote()

    def test_isolated_approval_cannot_publish_publicly(self):
        """Rehearsal approval must not authorize a public download channel."""
        with self.assertRaises(ValueError):
            release.promote(self.candidate, self.approval, self.target, "https://example.com")

    def test_restore_rejects_actual_bad_http_assets(self):
        """Recovery must reject a real corrupt download and a real HTTP 404."""
        initial = self.promote()
        snapshot = self.root / "snapshot.json"
        snapshot.write_bytes((self.target / release.INDEX).read_bytes())
        archive = self.target / "platform-0.1.0.tar.gz"
        archive.write_bytes(b"corrupt served bytes")
        with self.assertRaises(ValueError):
            release.restore(snapshot, self.target)
        archive.unlink()
        with self.assertRaises(OSError):
            release.restore(snapshot, self.target)
        self.assertEqual(initial, release.digest(self.target / release.INDEX))


if __name__ == "__main__":
    unittest.main()
