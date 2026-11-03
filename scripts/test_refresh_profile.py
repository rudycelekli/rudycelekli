"""Failure injection at the collection/publication boundary; no network calls."""

import contextlib
import datetime
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import refresh_profile as refresh


class RefreshSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in refresh.ARTIFACTS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(refresh.ROOT / name, path)
        (self.root / "scripts").mkdir()
        day = refresh.validate_bundle(self.root)
        real = datetime.datetime

        class Clock(real):
            @classmethod
            def now(cls, tz=None):
                return real(day.year, day.month, day.day, 12, tzinfo=tz)

        self.clock = mock.patch.object(refresh.dt, "datetime", Clock)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.before = refresh.snapshot(self.root)

    def valid_runner(self, args, cwd, env, **kwargs):
        proof = {
            "attempt_id": env["PROFILE_REFRESH_ATTEMPT_ID"],
            "snapshot_date": json.loads((cwd / "data/agentic-power.json").read_text())[
                "updated_at_utc"
            ],
            "sha256": {
                n: refresh.digest((cwd / n).read_bytes()) for n in refresh.ARTIFACTS
            },
        }
        Path(env["PROFILE_REFRESH_RECEIPT_PATH"]).write_text(json.dumps(proof))
        return SimpleNamespace(returncode=0)

    def assert_retained(self):
        for name in refresh.ARTIFACTS:
            if name == "README.md":
                continue
            self.assertEqual((self.root / name).read_bytes(), self.before[name], name)
        self.assertIn("Refresh delayed", (self.root / "README.md").read_text())
        self.assertIsNone(
            json.loads((self.root / refresh.HEALTH).read_text())[
                "last_successful_collection_utc"
            ]
        )

    def test_valid_complete_run_promotes_and_records_success(self):
        self.assertEqual(refresh.run_refresh(self.root, self.valid_runner), 0)
        health = json.loads((self.root / refresh.HEALTH).read_text())
        self.assertEqual(health["status"], "updated")
        self.assertIsNotNone(health["last_successful_collection_utc"])
        refresh.validate_bundle(self.root)

    def test_partial_collector_write_cannot_change_published_counts_or_leak_logs(self):
        def failed(args, cwd, stdout, **kwargs):
            (cwd / "data/contributions.json").write_text('{"partial": true}')
            stdout.write(b"PRIVATE repo-name secret diagnostic")
            return SimpleNamespace(returncode=1)

        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.assertEqual(refresh.run_refresh(self.root, failed), 1)
        self.assertNotIn("PRIVATE", log.getvalue())
        self.assert_retained()

    def test_timeout_retains_originals(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("collector", 1)

        self.assertEqual(refresh.run_refresh(self.root, timeout), 1)
        self.assert_retained()

    def test_exit_zero_without_receipt_is_failure(self):
        self.assertEqual(
            refresh.run_refresh(
                self.root, lambda *a, **k: SimpleNamespace(returncode=0)
            ),
            1,
        )
        self.assert_retained()

    def test_wrong_nonce_and_changed_file_invalidate_receipt(self):
        for change in ("nonce", "file"):
            with self.subTest(change=change):

                def runner(*args, **kwargs):
                    result = self.valid_runner(*args, **kwargs)
                    if change == "nonce":
                        proof = Path(kwargs["env"]["PROFILE_REFRESH_RECEIPT_PATH"])
                        data = json.loads(proof.read_text())
                        data["attempt_id"] = "old-run"
                        proof.write_text(json.dumps(data))
                    else:
                        (kwargs["cwd"] / "README.md").write_text(
                            "modified after receipt"
                        )
                    return result

                self.assertEqual(refresh.run_refresh(self.root, runner), 1)
                self.assert_retained()

    def test_semantic_corruption_is_rejected_even_with_matching_hashes(self):
        for change in (
            "json",
            "svg",
            "ratio",
            "months",
            "private_field",
            "private_nested",
        ):
            with self.subTest(change=change):

                def runner(*args, **kwargs):
                    stage = kwargs["cwd"]
                    if change == "json":
                        (stage / "data/contributions.json").write_text("{")
                    elif change == "svg":
                        (stage / "assets/hero.svg").write_text("<svg>")
                    elif change in ("private_field", "private_nested"):
                        path = stage / "data/owned-repository-work.json"
                        data = json.loads(path.read_text())
                        next(iter(data["receipts"].values()))[
                            "message"
                        ] = "private text"
                        path.write_text(json.dumps(data))
                    else:
                        path = stage / "data/agentic-power.json"
                        data = json.loads(path.read_text())
                        if change == "ratio":
                            data["monthly"][0]["agentic_power_x"]["base"] += 1
                        else:
                            data["monthly"].pop()
                        path.write_text(json.dumps(data))
                    return self.valid_runner(*args, **kwargs)

                self.assertEqual(refresh.run_refresh(self.root, runner), 1)
                self.assert_retained()

    def test_concurrent_user_edit_is_preserved_without_health_write(self):
        def runner(*args, **kwargs):
            (self.root / "README.md").write_text("user edit")
            return self.valid_runner(*args, **kwargs)

        self.assertEqual(refresh.run_refresh(self.root, runner), 1)
        self.assertEqual((self.root / "README.md").read_text(), "user edit")
        self.assertFalse((self.root / refresh.HEALTH).exists())

    def test_promotion_error_restores_original_files(self):
        original = refresh.replace_file
        calls = 0

        def fail_once(path, raw):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk failure")
            original(path, raw)

        with mock.patch.object(refresh, "replace_file", fail_once):
            with self.assertRaises(OSError):
                refresh.promote(
                    self.root,
                    self.before,
                    {"README.md": b"new", "assets/hero.svg": b"new"},
                )
        self.assertEqual(refresh.snapshot(self.root), self.before)

    def test_corrupted_health_can_be_replaced_by_valid_refresh(self):
        (self.root / refresh.HEALTH).write_text("{")
        self.assertEqual(refresh.run_refresh(self.root, self.valid_runner), 0)
        self.assertEqual(
            json.loads((self.root / refresh.HEALTH).read_text())["status"], "updated"
        )

    def test_invalid_baseline_reports_unavailable_without_claiming_private_coverage(
        self,
    ):
        (self.root / "data/owned-repository-work.json").write_text("{")
        self.assertEqual(
            refresh.run_refresh(
                self.root, lambda *a, **k: SimpleNamespace(returncode=1)
            ),
            1,
        )
        health = json.loads((self.root / refresh.HEALTH).read_text())
        self.assertEqual(health["status"], "unavailable")
        self.assertIsNone(health["private_through_utc"])

    def test_second_supervisor_does_not_enter_collection(self):
        def runner(*args, **kwargs):
            with mock.patch.object(refresh, "_run_refresh") as nested:
                self.assertEqual(refresh.run_refresh(self.root), 1)
                nested.assert_not_called()
            return self.valid_runner(*args, **kwargs)

        self.assertEqual(refresh.run_refresh(self.root, runner), 0)

    def test_stale_baseline_is_explicit_and_does_not_gain_success_timestamp(self):
        base = refresh.validate_bundle(self.root)
        real = datetime.datetime

        class Later(real):
            @classmethod
            def now(cls, tz=None):
                return real.combine(
                    base + datetime.timedelta(days=3), datetime.time(), tzinfo=tz
                )

        with mock.patch.object(refresh.dt, "datetime", Later):
            self.assertEqual(
                refresh.run_refresh(
                    self.root, lambda *a, **k: SimpleNamespace(returncode=1)
                ),
                1,
            )
        self.assertEqual(
            json.loads((self.root / refresh.HEALTH).read_text())["status"], "stale"
        )


if __name__ == "__main__":
    unittest.main()
