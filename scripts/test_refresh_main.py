"""Exercise the complete refresh entry point without live network or user files."""

import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import update_contributions as source


class RefreshEntryPointTests(unittest.TestCase):
    def test_complete_refresh_writes_artifacts_and_returns_success(self):
        published = json.loads(source.DATA_FILE.read_text())
        timing = json.loads(source.PR_TIMING_FILE.read_text())
        readme = source.README.read_text()
        repo = dict(published["repositories"][0])
        receipts = [
            row
            for row in published["merged_pull_requests"]
            if row["repository"] == repo["full_name"]
        ]
        repo["_merged_pull_requests"] = [
            {
                "number": row["number"],
                "title": row["title"],
                "url": row["url"],
                "createdAt": row["created_at"],
                "mergedAt": row["merged_at"],
                "additions": row["additions"],
                "deletions": row["deletions"],
                "changedFiles": row["changed_files"],
                **{
                    key: {"totalCount": row[key]}
                    for key in ("commits", "reviews", "comments")
                },
            }
            for row in receipts
        ]
        urls = {row["url"] for row in receipts}
        timing = {
            **timing,
            "pull_requests": {
                url: entry
                for url, entry in timing["pull_requests"].items()
                if url in urls
            },
        }
        as_of = datetime.date.fromisoformat(published["updated_at_utc"])
        original_datetime = datetime.datetime

        class SnapshotTime(original_datetime):
            @classmethod
            def now(cls, tz=None):
                return original_datetime(as_of.year, as_of.month, as_of.day, tzinfo=tz)

        first = source.agentic_work.month_windows(as_of.isoformat())[0]["month"] + "-01"
        owned = {
            "schema_version": 1,
            "public_from_utc": first,
            "private_from_utc": first,
            "public_through_utc": as_of.isoformat(),
            "private_through_utc": as_of.isoformat(),
            "public_repository_count": 0,
            "private_repository_count": 0,
            "receipts": {},
        }
        constants = {
            name: value
            for name, value in vars(source).items()
            if (name.endswith("_FILE") or name == "README") and isinstance(value, Path)
        }
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            root = Path(temporary)
            for name, value in constants.items():
                target = root / value.relative_to(source.ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                stack.enter_context(mock.patch.object(source, name, target))
            stack.enter_context(mock.patch.object(source, "ROOT", root))
            stack.enter_context(mock.patch.object(source.dt, "datetime", SnapshotTime))
            stack.enter_context(
                mock.patch.dict(
                    os.environ,
                    {
                        "GITHUB_TOKEN": "test-credential",
                        "PROFILE_REFRESH_RECEIPT_PATH": str(root / "completion.json"),
                        "PROFILE_REFRESH_ATTEMPT_ID": "integration-test",
                    },
                    clear=True,
                )
            )
            source.README.write_text(readme)
            stack.enter_context(
                mock.patch.object(source, "discover_repositories", return_value=[repo])
            )
            stack.enter_context(
                mock.patch.object(
                    source, "discover_current_open_pull_request_count", return_value=7
                )
            )
            stack.enter_context(
                mock.patch.object(
                    source,
                    "discover_owned_repositories",
                    return_value=published["owned_repositories"][:1],
                )
            )
            stack.enter_context(
                mock.patch.object(
                    source.agentic_work, "collect_pr_timelines", return_value=timing
                )
            )
            owner_collector = stack.enter_context(
                mock.patch.object(
                    source.agentic_work, "collect_owned_history", return_value=owned
                )
            )
            # Any accidental unmocked HTTP path fails rather than touching GitHub.
            stack.enter_context(
                mock.patch.object(
                    source,
                    "graphql_data",
                    side_effect=AssertionError("Unexpected network"),
                )
            )
            stack.enter_context(
                mock.patch.object(
                    source,
                    "rest_json",
                    side_effect=AssertionError("Unexpected network"),
                )
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(source.main(), 0)
            proof = json.loads((root / "completion.json").read_text())
            self.assertEqual(proof["attempt_id"], "integration-test")
            self.assertEqual(
                set(proof["sha256"]),
                {
                    str(path.relative_to(root))
                    for path in source.published_artifact_paths()
                },
            )
            for name, expected in proof["sha256"].items():
                self.assertEqual(
                    source.hashlib.sha256((root / name).read_bytes()).hexdigest(),
                    expected,
                )
            result = json.loads(source.AP_DATA_FILE.read_text())
            self.assertEqual(len(result["monthly"]), 12)
            self.assertEqual(result["direction_calibration"]["hours_per_day"], 2)
            self.assertEqual(
                result["public_accepted_merge_events"],
                sum(
                    result["window"]["start"]
                    <= row["merged_at"][:10]
                    <= as_of.isoformat()
                    for row in receipts
                ),
            )
            self.assertTrue(source.AP_SVG_FILE.is_file())
            self.assertTrue(source.OWNED_WORK_FILE.is_file())
            self.assertIn("approximately 14 hours a week", source.README.read_text())
            self.assertFalse(owner_collector.call_args.kwargs["include_private"])
            self.assertIn("Updated owned repository work", output.getvalue())


if __name__ == "__main__":
    unittest.main()
