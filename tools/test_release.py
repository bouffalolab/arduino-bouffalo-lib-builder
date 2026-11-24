#!/usr/bin/env python3
"""Host-only release tests with small fixture archives and a real loopback server."""

import functools
import hashlib
import http.server
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import release


class ReleaseFixture(unittest.TestCase):
    """Supply fixture archives and a real loopback server for host checks."""

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


class ReleaseTests(ReleaseFixture):
    """Check approval, asset ordering, immutable versions and index recovery."""

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

    def test_stable_channel_requires_final_release_approval(self):
        """Completed technical gates cannot substitute for final owner approval."""
        approval = release.load(self.approval)
        approval["channel"] = "stable"
        release.save(self.approval, approval)
        with self.assertRaisesRegex(ValueError, "ARDUINO-56"):
            self.promote()
        self.assertFalse((self.target / release.INDEX).exists())

    def test_unchanged_tool_retains_original_release_url(self):
        """A new platform release must reuse immutable tool versions from old tags."""
        index = release.load(self.candidate / release.INDEX)
        tool = {"name": "fixed-tool", "version": "1.0.0", "systems": [{
            "host": "x86_64-linux-gnu", "url": "https://example.com/v0.1.0/tool.tar.gz",
            "checksum": "SHA-256:" + "1" * 64, "size": "1", "archiveFileName": "tool.tar.gz"}]}
        index["packages"][0]["tools"].append(tool)
        incoming = release.load(self.candidate / release.INDEX)
        incoming["packages"][0]["tools"].append(release.copy.deepcopy(tool))
        incoming["packages"][0]["tools"][0]["systems"][0]["url"] = "https://example.com/v0.2.0/tool.tar.gz"
        merged = release.merge(index, incoming)
        self.assertEqual(tool, merged["packages"][0]["tools"][0])


class VerifyDownloadsTests(unittest.TestCase):
    """Publication must still check each asset's size and SHA-256 without re-downloading
    GitHub Release assets. gh API answers and downloads are test doubles."""

    RELEASE = "https://github.com/bouffalolab/arduino-bouffalo/releases/download/v0.2.0/"

    @staticmethod
    def entry(url, payload):
        """Describe an asset as the index would for the given bytes."""
        return {"url": url, "size": str(len(payload)),
                "checksum": "SHA-256:" + hashlib.sha256(payload).hexdigest()}

    @staticmethod
    def index(*entries):
        """Wrap download descriptors as host-tool systems of one package."""
        return {"packages": [{"platforms": [], "tools": [{"systems": list(entries)}]}]}

    @staticmethod
    def asset(url, payload, digest=True):
        """Return GitHub's metadata for an uploaded asset; old uploads may lack a digest."""
        return {"browser_download_url": url, "size": len(payload),
                "digest": "sha256:" + hashlib.sha256(payload).hexdigest() if digest else None}

    def verify(self, index, assets, downloads=None):
        """Run verify_downloads; return the gh API calls and the URLs actually downloaded."""
        api, fetched = [], []

        def gh(args, text):
            """Answer the release lookup with the given asset metadata."""
            api.append(args)
            return json.dumps({"assets": assets})

        def urlopen(url, timeout):
            """Serve test bytes and record that a download happened."""
            fetched.append(url)
            return io.BytesIO((downloads or {})[url])

        with patch("release.subprocess.check_output", side_effect=gh), \
                patch("release.urlopen", side_effect=urlopen):
            release.verify_downloads(index)
        return api, fetched

    def test_github_assets_use_api_metadata_without_download(self):
        """One API call covers a release; a URL listed twice is checked once, never fetched."""
        platform, tool = self.RELEASE + "platform.tar.gz", self.RELEASE + "tool.tar.gz"
        api, fetched = self.verify(
            self.index(self.entry(platform, b"platform"), self.entry(tool, b"tool"),
                       self.entry(tool, b"tool")),
            [self.asset(platform, b"platform"), self.asset(tool, b"tool")])
        self.assertEqual([["gh", "api", "repos/bouffalolab/arduino-bouffalo/releases/tags/v0.2.0"]], api)
        self.assertEqual([], fetched)

    def test_size_or_digest_mismatch_is_rejected(self):
        """An index entry that disagrees with GitHub's metadata must not be published."""
        url = self.RELEASE + "tool.tar.gz"
        for published in ({**self.asset(url, b"tool"), "size": 5},
                          self.asset(url, b"other bytes")):
            with self.assertRaisesRegex(ValueError, "download identity mismatch"):
                self.verify(self.index(self.entry(url, b"tool")), [published])

    def test_asset_without_digest_is_downloaded_once(self):
        """Without GitHub's digest the bytes are fetched and hashed, once per URL."""
        url = self.RELEASE + "old.tar.gz"
        assets = [self.asset(url, b"old", digest=False)]
        _, fetched = self.verify(self.index(self.entry(url, b"old"), self.entry(url, b"old")),
                                 assets, {url: b"old"})
        self.assertEqual([url], fetched)
        with self.assertRaisesRegex(ValueError, "download identity mismatch"):
            self.verify(self.index(self.entry(url, b"old")), assets, {url: b"changed"})


if __name__ == "__main__":
    unittest.main()
