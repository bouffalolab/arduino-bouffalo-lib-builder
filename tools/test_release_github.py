#!/usr/bin/env python3
"""Host-only GitHub adapter checks; subprocess/API responses are explicit test doubles."""

import subprocess
from unittest.mock import patch

import release
import release_github
from test_release import ReleaseFixture


class GithubReleaseTests(ReleaseFixture):
    """Use fixture archives to verify planning and pre-write rejection."""

    def test_plan_is_read_only_and_pins_target(self):
        """Generating a plan must not call GitHub or create remote objects."""
        with patch("release_github.command", side_effect=AssertionError("unexpected remote call")):
            plan = release_github.prepare(self.candidate)
        self.assertEqual("v0.1.0", plan["tag"])
        self.assertEqual("1" * 40, plan["target"])
        self.assertEqual("master", plan["index_branch"])

    def test_isolated_approval_rejected_before_remote_calls(self):
        """A rehearsed candidate is not authorized for public asset writes."""
        plan = release_github.prepare(self.candidate)
        with patch("release_github.command", side_effect=AssertionError("unexpected remote call")):
            with self.assertRaises(ValueError):
                release_github.execute(self.candidate, self.approval, plan)

    def test_api_failure_is_not_absent_index(self):
        """A permission/server failure cannot silently discard published versions."""
        response = subprocess.CompletedProcess([], 1, "", "gh: HTTP 403 Forbidden")
        with patch("release_github.subprocess.run", return_value=response):
            with self.assertRaises(RuntimeError):
                release_github.existing_index()

    def test_publish_assets_before_index_and_stop_on_download_failure(self):
        """Mock remote writes to check ordering, and keep HTTP failures off master."""
        plan = release_github.prepare(self.candidate)
        approval = release.load(self.approval)
        approval.update(channel="stable", **{"ARDUINO-56": "host test fixture only"})
        release.save(self.approval, approval)
        events = []

        def fake_command(*args):
            """Record the fake GitHub operation without sending network requests."""
            events.append(args[:3])
            return "[]" if args[:2] == ("gh", "api") else ""

        def fake_verify(index):
            """Represent the already-tested HTTP boundary for adapter ordering only."""
            events.append(("verify",))

        with patch("release_github.existing_index", return_value=({"packages": []}, None)), \
                patch("release_github.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch("release_github.command", side_effect=fake_command), \
                patch("release_github.release.verify_downloads", side_effect=fake_verify), \
                patch("release_github.update_index", side_effect=lambda *args: events.append(("index",))):
            release_github.execute(self.candidate, self.approval, plan)
        self.assertLess(events.index(("gh", "release", "edit")), events.index(("verify",)))
        self.assertEqual(("index",), events[-1])
        with patch("release_github.existing_index", return_value=({"packages": []}, None)), \
                patch("release_github.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                patch("release_github.command", side_effect=fake_command), \
                patch("release_github.release.verify_downloads", side_effect=OSError("mock HTTP failure")), \
                patch("release_github.update_index") as update:
            with self.assertRaises(OSError):
                release_github.execute(self.candidate, self.approval, plan)
            update.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
