import copy
import datetime as dt
import json
import unittest
import traceback
from unittest import mock

from scripts import agentic_work as work
from scripts import update_contributions as source


def pr(number=1, merged="2026-11-02T00:00:00Z"):
    return {
        "number": number,
        "url": f"https://github.com/upstream/engine/pull/{number}",
        "title": "fix: preserve valid data",
        "createdAt": "2026-09-01T00:00:00Z",
        "mergedAt": merged,
        "additions": 25,
        "deletions": 5,
        "changedFiles": 3,
        "commits": {"totalCount": 1},
        "comments": {"totalCount": 0},
        "reviews": {"totalCount": 0},
    }


def commit(sha="a" * 40, date="2026-09-02T00:00:00Z", author="rudycelekli"):
    return {"sha": sha, "authored_at": date, "author_login": author, "parent_count": 1}


def owned_receipt(month="2026-09", private=True, base=1.5):
    return {
        "month": month,
        "visibility": "private" if private else "public",
        "category": "fix_and_refactor",
        "heh": {
            "low": round(base * 0.7, 2),
            "base": base,
            "high": round(base * 1.4, 2),
        },
    }


def owned(updated="2026-11-03", receipts=None):
    return {
        "schema_version": 1,
        "public_through_utc": updated,
        "public_from_utc": work.month_windows(updated)[0]["month"] + "-01",
        "private_through_utc": updated,
        "private_from_utc": work.month_windows(updated)[0]["month"] + "-01",
        "public_repository_count": 0,
        "private_repository_count": 1,
        "receipts": receipts or {},
    }


def profile(prs=None, dates=None, updated="2026-11-03", owned_data=None):
    prs = [pr()] if prs is None else prs
    dates = [commit()] if dates is None else dates
    timings = {
        "pull_requests": {
            row["url"]: {"commit_count": len(dates), "commits": dates} for row in prs
        }
    }
    return source.calculate_agentic_power(
        [{"full_name": "upstream/engine", "_merged_pull_requests": prs}],
        updated,
        owned(updated) if owned_data is None else owned_data,
        timings,
    )


def month(data, name):
    return next(row for row in data["monthly"] if row["month"] == name)


class RollingMonthTests(unittest.TestCase):
    def test_exactly_twelve_calendar_buckets_and_fixed_daily_denominator(self):
        data = profile()
        self.assertEqual(len(data["monthly"]), 12)
        self.assertEqual(data["window"]["start"], "2025-12-01")
        self.assertEqual(data["monthly"][-1]["month"], "2026-11")
        self.assertEqual(month(data, "2026-09")["direction_hours"], 60)
        self.assertEqual(month(data, "2026-10")["direction_hours"], 62)
        self.assertEqual(month(data, "2026-11")["direction_hours"], 6)
        self.assertEqual(data["direction_calibration"]["hours_per_week"], 14)
        self.assertEqual(data["modeled_human_direction_hours"]["base"], 676)

    def test_empty_complete_scope_is_zero_but_retains_all_calendar_hours(self):
        data = profile(prs=[], dates=[])
        self.assertEqual(data["agentic_power_x"]["base"], 0)
        self.assertEqual(month(data, "2026-09")["agentic_power_x"]["base"], 0)
        self.assertEqual(data["modeled_human_direction_hours"]["base"], 676)

    def test_late_acceptance_backfills_work_month_not_merge_month(self):
        before = profile(updated="2026-10-31")
        after = profile()
        self.assertEqual(
            month(before, "2026-09")["skilled_human_equivalent_hours"]["base"], 0
        )
        self.assertGreater(
            month(after, "2026-09")["skilled_human_equivalent_hours"]["base"], 0
        )
        self.assertEqual(
            month(after, "2026-11")["skilled_human_equivalent_hours"]["base"], 0
        )
        self.assertEqual(month(after, "2026-11")["public_merged_pull_requests"], 1)
        self.assertEqual(month(before, "2026-10"), month(after, "2026-10"))

    def test_later_work_does_not_rescale_completed_month(self):
        old = profile([pr(1, "2026-09-30T00:00:00Z")], updated="2026-09-30")
        current = profile([pr(1, "2026-09-30T00:00:00Z"), pr(2)])
        # A second accepted PR with work dated October must not change September.
        cache = {
            "pull_requests": {
                pr(1)["url"]: {"commit_count": 1, "commits": [commit()]},
                pr(2)["url"]: {
                    "commit_count": 1,
                    "commits": [commit("b" * 40, "2026-10-10T00:00:00Z")],
                },
            }
        }
        current = source.calculate_agentic_power(
            [
                {
                    "full_name": "upstream/engine",
                    "_merged_pull_requests": [pr(1, "2026-09-30T00:00:00Z"), pr(2)],
                }
            ],
            "2026-11-03",
            owned(),
            cache,
        )
        self.assertEqual(month(old, "2026-09"), month(current, "2026-09"))

    def test_pr_heh_is_conserved_across_commit_months(self):
        row = pr()
        row["commits"]["totalCount"] = 3
        data = profile(
            [row],
            [commit(), commit("b" * 40), commit("c" * 40, "2026-10-02T00:00:00Z")],
        )
        item = data["work_items"][0]
        for key in ("low", "base", "high"):
            self.assertAlmostEqual(
                sum(value[key] for value in item["monthly_heh_allocation"].values()),
                item["heh"][key],
                places=2,
            )
        self.assertEqual(data["public_accepted_pull_requests"], 1)
        self.assertEqual(month(data, "2026-09")["public_authored_pr_commits"], 2)
        self.assertEqual(month(data, "2026-10")["public_authored_pr_commits"], 1)

    def test_rounding_allocates_every_cent_once(self):
        self.assertEqual(
            work.allocate_hours(0.01, {"2026-01": 1, "2026-02": 1, "2026-03": 1}),
            {"2026-01": 0.01, "2026-02": 0, "2026-03": 0},
        )

    def test_utc_authored_date_controls_month(self):
        data = profile(dates=[commit(date="2026-01-01T00:30:00+02:00")])
        self.assertEqual(month(data, "2025-12")["public_authored_pr_commits"], 1)
        self.assertEqual(month(data, "2026-01")["public_authored_pr_commits"], 0)

    def test_work_outside_window_is_not_shifted_into_acceptance_month(self):
        data = profile(dates=[commit(date="2025-11-30T23:00:00Z")])
        self.assertEqual(data["skilled_human_equivalent_hours"]["base"], 0)
        self.assertEqual(month(data, "2026-11")["public_merged_pull_requests"], 1)

    def test_duplicate_pr_receipts_do_not_double_count(self):
        one = profile()
        twice = profile([pr(), pr()])
        self.assertEqual(
            one["skilled_human_equivalent_hours"],
            twice["skilled_human_equivalent_hours"],
        )
        duplicate = pr()
        duplicate["additions"] = 26
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            profile([pr(), duplicate])

    def test_open_prs_and_future_merges_are_not_accepted_work(self):
        data = profile([pr(1, None), pr(2, "2026-11-04T00:00:00Z")])
        self.assertEqual(data["public_accepted_pull_requests"], 0)

    def test_owned_commit_already_modeled_in_public_pr_is_excluded(self):
        data = profile(
            owned_data=owned(receipts={work.digest("a" * 40): owned_receipt()})
        )
        self.assertEqual(data["owned_private_commits"], 0)
        self.assertEqual(data["excluded_owned_pr_overlap_commits"], 1)
        self.assertEqual(
            data["skilled_human_equivalent_hours"],
            profile()["skilled_human_equivalent_hours"],
        )

    def test_missing_private_months_are_unknown_not_false_zero(self):
        cache = owned()
        cache["private_through_utc"] = "2026-09-30"
        data = profile(prs=[], dates=[], owned_data=cache)
        self.assertEqual(month(data, "2026-09")["coverage_status"], "complete")
        self.assertEqual(month(data, "2026-10")["coverage_status"], "partial")
        self.assertIsNone(month(data, "2026-10")["agentic_power_x"]["base"])

    def test_monthly_only_legacy_private_snapshot_is_not_interpolated(self):
        data = profile(
            prs=[], dates=[], owned_data={"monthly": {"2026-01": {"heh": 99999}}}
        )
        self.assertEqual(data["skilled_human_equivalent_hours"]["base"], 0)
        self.assertTrue(
            all(row["coverage_status"] == "partial" for row in data["monthly"])
        )

    def test_creation_month_fallback_is_explicit(self):
        data = profile(dates=[commit(author="another-contributor")])
        self.assertEqual(data["creation_month_proxy_prs"], 1)
        self.assertIn(
            "creation-month proxy", data["work_items"][0]["work_timing_basis"]
        )

    def test_invalid_statistics_timestamps_and_partial_pages_fail_closed(self):
        bad = pr()
        bad["additions"] = -1
        with self.assertRaises(ValueError):
            profile([bad])
        with self.assertRaises(ValueError):
            profile(dates=[commit(date="2026-12-01T00:00:00Z")])
        with self.assertRaises(ValueError):
            profile(dates=[commit(date="2026-09-01T00:00:00")])
        with self.assertRaises(ValueError):
            profile(dates=[])
        with self.assertRaises(ValueError):
            profile(
                owned_data=owned(receipts={"b" * 64: owned_receipt(base=float("nan"))})
            )

    def test_private_identity_fields_cannot_enter_public_receipts(self):
        row = owned_receipt()
        row["repository"] = "secret-client"
        with self.assertRaisesRegex(ValueError, "identity fields"):
            profile(owned_data=owned(receipts={"b" * 64: row}))

    def test_rollover_drops_one_month_and_keeps_overlap(self):
        before = profile(prs=[], dates=[], updated="2026-11-30")
        after = profile(prs=[], dates=[], updated="2026-12-01")
        self.assertEqual(after["window"]["start"], "2026-01-01")
        self.assertEqual(month(before, "2026-10"), month(after, "2026-10"))
        self.assertEqual(after["monthly"][-1]["direction_hours"], 2)
        leap = work.month_windows("2028-03-01")
        self.assertEqual(
            next(m for m in leap if m["month"] == "2028-02")["direction_hours"], 58
        )

    def test_overall_ratio_is_ratio_of_sums_not_average_monthly_ratios(self):
        data = profile(
            owned_data=owned(receipts={"b" * 64: owned_receipt("2026-11", base=20)})
        )
        expected = round(
            sum(m["skilled_human_equivalent_hours"]["base"] for m in data["monthly"])
            / 676,
            2,
        )
        self.assertEqual(data["agentic_power_x"]["base"], expected)
        self.assertNotEqual(
            expected,
            round(sum(m["agentic_power_x"]["base"] for m in data["monthly"]) / 12, 2),
        )

    def test_chart_and_note_explain_real_monthly_ratio(self):
        data = profile()
        svg = source.render_agentic_power_svg(data)
        note = source.render_agentic_power_readme(data)
        self.assertIn("LINE: MONTHLY AP", svg)
        self.assertIn('fill="none"', svg)
        self.assertIn("14 h/week", svg)
        self.assertIn("authored months", note)
        self.assertIn("2026-11 (partial month)", note)
        self.assertIn("| 6 |", note)

    def test_public_reuse_is_credited_once_in_acceptance_order(self):
        earliest = pr(1, "2026-09-30T00:00:00Z")
        data = profile([pr(2), earliest])
        self.assertEqual(
            data["skilled_human_equivalent_hours"],
            profile([earliest])["skilled_human_equivalent_hours"],
        )
        self.assertEqual(data["public_accepted_merge_events"], 2)
        self.assertEqual(data["public_accepted_pull_requests"], 1)
        self.assertEqual(data["public_reused_commit_entries_excluded"], 1)
        self.assertEqual(data, profile([earliest, pr(2)]))

    def test_partial_public_reuse_reduces_estimate_and_keeps_original_work_fixed(self):
        early = pr(1, "2026-09-30T00:00:00Z")
        late = pr(2)
        late["commits"]["totalCount"] = 3
        entries = {
            early["url"]: {"commit_count": 1, "commits": [commit()]},
            late["url"]: {
                "commit_count": 3,
                "commits": [
                    commit(),
                    commit("b" * 40, "2026-10-02T00:00:00Z"),
                    commit("c" * 40, "2026-10-03T00:00:00Z"),
                ],
            },
        }
        data = source.calculate_agentic_power(
            [{"full_name": "upstream/engine", "_merged_pull_requests": [late, early]}],
            "2026-11-03",
            owned(),
            {"pull_requests": entries},
        )
        second = next(item for item in data["work_items"] if item["pull_request"] == 2)
        self.assertLess(
            second["heh"]["base"], second["heh_before_reuse_adjustment"]["base"]
        )
        self.assertEqual(list(second["monthly_heh_allocation"]), ["2026-10"])
        self.assertEqual(second["heh"], {"low": 4.51, "base": 6.01, "high": 8.11})
        for key in ("low", "base", "high"):
            self.assertAlmostEqual(
                sum(row[key] for row in second["monthly_heh_allocation"].values()),
                second["heh"][key],
                places=2,
            )
        self.assertEqual(
            month(data, "2026-09")["skilled_human_equivalent_hours"],
            month(profile([early]), "2026-09")["skilled_human_equivalent_hours"],
        )

    def test_later_window_cache_does_not_claim_older_missing_coverage(self):
        data = profile(
            prs=[], dates=[], updated="2026-10-31", owned_data=owned("2026-11-03")
        )
        self.assertEqual(month(data, "2025-11")["coverage_status"], "partial")
        self.assertEqual(data["coverage_status"], "partial")
        self.assertIn(
            "Recorded-work ratio (partial evidence)",
            source.render_agentic_power_svg(data),
        )


class CollectionTests(unittest.TestCase):
    def test_owned_scope_deduplication_and_private_redaction(self):
        def raw(name, sha, private=True, fork=False, parents=1):
            return {
                "sha": sha,
                "repository": {"full_name": name, "private": private, "fork": fork},
                "parents": [{}] * parents,
                "commit": {
                    "author": {"date": "2026-09-02T00:00:00Z"},
                    "message": "fix: PRIVATE_CLIENT_SECRET",
                },
            }

        rows = [
            raw("rudycelekli/PRIVATE_REPO", "a" * 40),
            raw("rudycelekli/public", "a" * 40, False),
            raw("employer/client", "b" * 40),
            raw("rudycelekli/fork", "c" * 40, fork=True),
            raw("rudycelekli/rudycelekli", "d" * 40),
            raw("rudycelekli/private", "e" * 40, parents=2),
        ]
        data = work.collect_owned_work(
            "2026-11-03",
            "rudycelekli",
            lambda a, b: rows,
            include_private=True,
            exclusions={"rudycelekli/rudycelekli"},
        )
        self.assertEqual(len(data["receipts"]), 1)
        self.assertEqual(
            data["receipts"][work.digest("a" * 40)]["visibility"], "public"
        )
        text = json.dumps(data)
        for secret in (
            "PRIVATE_REPO",
            "PRIVATE_CLIENT_SECRET",
            "a" * 40,
            "2026-09-02T00:00:00Z",
            "employer/client",
        ):
            self.assertNotIn(secret, text)

    def test_public_refresh_retains_dated_private_receipts(self):
        old = owned("2026-09-30", {"b" * 64: owned_receipt()})
        new = work.collect_owned_work(
            "2026-11-03", "rudycelekli", lambda a, b: [], cached=old
        )
        self.assertEqual(new["private_through_utc"], "2026-09-30")
        self.assertEqual(new["public_through_utc"], "2026-11-03")
        self.assertEqual(new["receipts"], old["receipts"])

    def test_complete_public_timeline_cache_avoids_refetch(self):
        cache = {
            "pull_requests": {pr()["url"]: {"commit_count": 1, "commits": [commit()]}}
        }
        graphql = mock.Mock(side_effect=AssertionError("unexpected network"))
        result = work.collect_pr_timelines(
            "token",
            [{"full_name": "upstream/engine", "_merged_pull_requests": [pr()]}],
            cache,
            graphql,
        )
        self.assertEqual(result["pull_requests"], cache["pull_requests"])

    def test_cache_drops_prs_outside_current_approved_public_scope(self):
        cache = {
            "pull_requests": {
                "https://github.com/excluded/project/pull/1": {
                    "commit_count": 1,
                    "commits": [commit()],
                }
            }
        }
        result = work.collect_pr_timelines(
            "token", [], cache, mock.Mock(side_effect=AssertionError("network"))
        )
        self.assertEqual(result["pull_requests"], {})

    def test_large_pr_uses_validated_paginated_compare_after_250_cap(self):
        row = pr()
        row["commits"]["totalCount"] = 300

        def node(i):
            return {
                "commit": {
                    "oid": f"{i:040x}",
                    "authoredDate": "2026-09-02T00:00:00Z",
                    "author": {"user": {"login": "rudycelekli"}},
                    "parents": {"totalCount": 1},
                }
            }

        def connection(a, b, more):
            return {
                "totalCount": 300,
                "pageInfo": {"hasNextPage": more, "endCursor": str(b)},
                "nodes": [node(i) for i in range(a, b)],
            }

        graphql = mock.Mock(
            side_effect=[
                {"r0": {"pullRequest": {"commits": connection(0, 100, True)}}},
                {
                    "repository": {
                        "pullRequest": {"commits": connection(100, 200, True)}
                    }
                },
                {
                    "repository": {
                        "pullRequest": {"commits": connection(200, 250, False)}
                    }
                },
            ]
        )
        responses = [{"base": {"sha": "base"}, "head": {"sha": "head"}}]
        for page in range(3):
            responses.append(
                {
                    "total_commits": 300,
                    "commits": [
                        {
                            "sha": f"{i:040x}",
                            "commit": {"author": {"date": "2026-09-02T00:00:00Z"}},
                            "author": {"login": "rudycelekli"},
                            "parents": [{}],
                        }
                        for i in range(page * 100, (page + 1) * 100)
                    ],
                }
            )
        rest = mock.Mock(side_effect=responses)
        data = work.collect_pr_timelines(
            "token",
            [{"full_name": "upstream/engine", "_merged_pull_requests": [row]}],
            None,
            graphql,
            rest=rest,
        )
        self.assertEqual(len(data["pull_requests"][row["url"]]["commits"]), 300)
        self.assertEqual(rest.call_count, 4)

    def test_unfinished_commit_connection_cannot_be_called_complete(self):
        c = {
            "totalCount": 2,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [],
        }
        with self.assertRaises(RuntimeError):
            work.collect_pr_timelines(
                "token",
                [{"full_name": "upstream/engine", "_merged_pull_requests": [pr()]}],
                None,
                lambda *args: {"r0": {"pullRequest": {"commits": c}}},
            )


class PinnedOwnedHistoryTests(unittest.TestCase):
    def inventory(self, count=1):
        return {
            "user": {
                "id": "ACCOUNT",
                "repositories": {
                    "totalCount": count,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "nameWithOwner": "rudycelekli/PRIVATE_REPO",
                            "isPrivate": True,
                            "isFork": False,
                            "defaultBranchRef": {"target": {"oid": "PINNED_HEAD"}},
                        }
                    ],
                },
            }
        }

    def connection(self, rows, more=False, total=2):
        return {
            "totalCount": total,
            "pageInfo": {"hasNextPage": more, "endCursor": "NEXT"},
            "nodes": rows,
        }

    def node(self, sha):
        return {
            "oid": sha,
            "authoredDate": "2026-09-02T00:00:00Z",
            "messageHeadline": "fix: PRIVATE_COMMIT_TEXT",
            "parents": {"totalCount": 1},
        }

    def test_owned_paging_stays_on_pinned_head_and_redacts_metadata(self):
        first = self.connection([self.node("a" * 40)], more=True)
        second = self.connection([self.node("b" * 40)])
        api = mock.Mock(
            side_effect=[
                self.inventory(),
                {"r0": {"object": {"history": first}}},
                {"repository": {"object": {"history": second}}},
            ]
        )
        result = work.collect_owned_history(
            "credential", "2026-11-03", "rudycelekli", api, include_private=True
        )
        self.assertEqual(len(result["receipts"]), 2)
        self.assertEqual(api.call_args_list[2].args[2]["head"], "PINNED_HEAD")
        self.assertEqual(api.call_args_list[2].args[2]["author"], "ACCOUNT")
        encoded = json.dumps(result)
        for secret in ("PRIVATE_REPO", "PRIVATE_COMMIT_TEXT", "PINNED_HEAD", "a" * 40):
            self.assertNotIn(secret, encoded)

    def test_incomplete_inventory_and_history_are_rejected(self):
        api = mock.Mock(return_value=self.inventory(count=2))
        with self.assertRaisesRegex(RuntimeError, "private details withheld"):
            work.collect_owned_history(
                "token", "2026-11-03", "rudycelekli", api, include_private=True
            )
        api = mock.Mock(
            side_effect=[
                self.inventory(),
                {"r0": {"object": {"history": self.connection([self.node("a" * 40)])}}},
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "private details withheld"):
            work.collect_owned_history(
                "token", "2026-11-03", "rudycelekli", api, include_private=True
            )

    def test_private_api_errors_do_not_leak_repository_names_in_public_logs(self):
        api = mock.Mock(
            side_effect=RuntimeError("Could not resolve PRIVATE_REPOSITORY_NAME")
        )
        try:
            work.collect_owned_history(
                "token", "2026-11-03", "rudycelekli", api, include_private=True
            )
        except RuntimeError as error:
            output = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
            self.assertIn("private details withheld", output)
            self.assertNotIn("PRIVATE_REPOSITORY_NAME", output)
        else:
            self.fail("Failed private collection must not be accepted")

    def test_public_collector_refuses_private_identity_disclosure(self):
        with self.assertRaisesRegex(RuntimeError, "private repository"):
            work.collect_pr_timelines(
                "token",
                [{"full_name": "upstream/engine", "_merged_pull_requests": [pr()]}],
                None,
                lambda *args: {"r0": {"isPrivate": True}},
            )


if __name__ == "__main__":
    unittest.main()
