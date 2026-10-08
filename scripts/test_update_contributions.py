import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import update_contributions as subject


def pull_request(repository, number=1, merged=True):
    return {
        "number": number,
        "title": "fix: accepted upstream work",
        "createdAt": "2026-09-01T00:00:00Z",
        "mergedAt": "2026-09-02T00:00:00Z" if merged else None,
        "merged": merged,
        "additions": 25,
        "deletions": 5,
        "changedFiles": 3,
        "url": f"{repository['url']}/pull/{number}",
        "commits": {"totalCount": 1},
        "comments": {"totalCount": 0},
        "reviews": {"totalCount": 1},
        "repository": repository,
    }


def repository(full_name, **overrides):
    owner, name = full_name.split("/", 1)
    value = {
        "name": name,
        "nameWithOwner": full_name,
        "description": "A public upstream project with accepted contributions",
        "url": f"https://github.com/{full_name}",
        "isPrivate": False,
        "visibility": "PUBLIC",
        "isFork": False,
        "stargazerCount": 1_234,
        "forkCount": 56,
        "owner": {"login": owner},
    }
    value.update(overrides)
    return value


def owned_repository(full_name, **overrides):
    owner, name = full_name.split("/", 1)
    value = {
        "name": name,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "description": "An owned public source project",
        "homepage": "",
        "language": "Python",
        "topics": ["agents"],
        "stargazers_count": 0,
        "forks_count": 0,
        "private": False,
        "fork": False,
        "archived": False,
        "disabled": False,
        "pushed_at": "2026-09-30T00:00:00Z",
        "created_at": "2026-01-01T00:00:00Z",
        "owner": {"login": owner},
    }
    value.update(overrides)
    return value


class ContributionDiscoveryTests(unittest.TestCase):
    @mock.patch.object(subject.time, "sleep")
    @mock.patch.object(subject.urllib.request, "urlopen")
    def test_rest_json_retries_an_empty_success_body(self, urlopen, sleep):
        empty_response = mock.MagicMock()
        empty_response.__enter__.return_value = io.BytesIO(b"")
        valid_response = mock.MagicMock()
        valid_response.__enter__.return_value = io.BytesIO(b'[{"ok": true}]')
        urlopen.side_effect = [empty_response, valid_response]

        payload = subject.rest_json("token", "https://api.github.com/example")

        self.assertEqual(payload, [{"ok": True}])
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    @mock.patch.object(subject, "rest_json")
    def test_official_contribution_stats_returns_all_time_rank(self, rest_json):
        rest_json.return_value = [
            {"login": "first", "contributions": 90},
            {"login": subject.LOGIN, "contributions": 42},
            {"login": "third", "contributions": 12},
        ]

        count, rank = subject.official_contribution_stats(
            "token", "upstream/project"
        )

        self.assertEqual((count, rank), (42, 2))

    @mock.patch.object(subject, "rest_json")
    def test_official_contribution_stats_uses_cached_evidence_when_pending(
        self, rest_json
    ):
        rest_json.side_effect = RuntimeError("pending contributor index")
        with tempfile.TemporaryDirectory() as directory:
            data_file = Path(directory) / "contributions.json"
            data_file.write_text(
                json.dumps(
                    {
                        "repositories": [
                            {
                                "full_name": "upstream/project",
                                "official_contributor": True,
                                "verification_tier": "github_listed_contributor",
                                "contributor_commits": 41,
                                "contributor_rank": 3,
                            }
                        ]
                    }
                )
            )
            with mock.patch.object(subject, "DATA_FILE", data_file):
                stats = subject.official_contribution_stats(
                    "token", "upstream/project"
                )

        self.assertEqual(stats, (41, 3))

    def test_cached_graph_evidence_does_not_impersonate_official_index(self):
        with tempfile.TemporaryDirectory() as directory:
            data_file = Path(directory) / "contributions.json"
            data_file.write_text(
                json.dumps(
                    {
                        "repositories": [
                            {
                                "full_name": "earthtojake/text-to-cad",
                                "official_contributor": False,
                                "verification_tier": (
                                    "github_contribution_graph_verified"
                                ),
                                "contributor_commits": 1,
                                "contributor_rank": None,
                            }
                        ]
                    }
                )
            )
            with mock.patch.object(subject, "DATA_FILE", data_file):
                stats = subject.cached_contribution_stats(
                    "earthtojake/text-to-cad"
                )

        self.assertEqual(stats, (None, None))

    @mock.patch.object(subject, "graphql")
    def test_search_splits_windows_beyond_githubs_result_cap(self, graphql):
        left = pull_request(repository("upstream/left"), 1)
        right = pull_request(repository("upstream/right"), 2)
        graphql.side_effect = [
            {
                "issueCount": 1_001,
                "pageInfo": {"hasNextPage": True, "endCursor": "ignored"},
                "nodes": [],
            },
            {
                "issueCount": 1,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [left],
            },
            {
                "issueCount": 1,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [right],
            },
        ]

        results = subject.search_merged_pull_requests(
            "token", subject.dt.date(2026, 1, 1), subject.dt.date(2026, 1, 2)
        )

        self.assertEqual([item["number"] for item in results], [1, 2])
        self.assertEqual(graphql.call_count, 3)

    @mock.patch.object(subject, "default_branch_contribution_count")
    @mock.patch.object(subject, "official_contribution_stats")
    @mock.patch.object(subject, "discover_commit_contribution_repositories")
    @mock.patch.object(subject, "search_authored_pull_requests")
    def test_discovers_and_upgrades_each_accepted_evidence_tier(
        self, authored_search, graph_search, official_stats, default_count
    ):
        accepted = repository("upstream/new-project")
        pending = repository("upstream/pending-index")
        default_only = repository("upstream/default-only")
        unaccepted = repository("upstream/open-only")
        private = repository("company/private", isPrivate=True)
        internal = repository("company/internal", visibility="INTERNAL")
        owned = repository(f"{subject.LOGIN}/owned-project")
        authored_search.return_value = [
            pull_request(accepted, 6),
            pull_request(pending, 5),
            pull_request(default_only, 4, merged=False),
            pull_request(unaccepted, 3, merged=False),
            pull_request(private, 2),
            pull_request(internal, 7),
            pull_request(owned, 1),
        ]
        graph_search.return_value = {}
        official_stats.side_effect = lambda _token, full_name: (
            (7, 3) if full_name == "upstream/new-project" else (None, None)
        )
        default_count.side_effect = lambda _token, full_name: {
            "upstream/pending-index": 0,
            "upstream/default-only": 4,
            "upstream/open-only": 0,
        }[full_name]

        discovered = subject.discover_repositories("token")

        self.assertEqual(
            [item["full_name"] for item in discovered],
            [
                "upstream/new-project",
                "upstream/pending-index",
                "upstream/default-only",
            ],
        )
        self.assertEqual(discovered[0]["merged_prs"], 1)
        self.assertEqual(discovered[0]["contributor_commits"], 7)
        self.assertEqual(discovered[0]["contributor_rank"], 3)
        self.assertEqual(discovered[0]["verification_tier"], "github_listed_contributor")
        self.assertEqual(discovered[1]["verification_tier"], "merged_pr_verified")
        self.assertEqual(discovered[1]["contributor_commits"], 0)
        self.assertEqual(
            discovered[2]["verification_tier"],
            "default_branch_commit_verified",
        )
        self.assertEqual(discovered[2]["contributor_commits"], 4)
        self.assertEqual(discovered[2]["merged_prs"], 0)
        self.assertEqual(discovered[0]["stargazers"], 1_234)
        self.assertEqual(discovered[0]["forks"], 56)
        self.assertEqual(
            [call.args[1] for call in official_stats.call_args_list],
            [
                "upstream/new-project",
                "upstream/pending-index",
                "upstream/default-only",
            ],
        )

    @mock.patch.object(subject, "default_branch_contribution_count", return_value=0)
    @mock.patch.object(
        subject, "official_contribution_stats", return_value=(None, None)
    )
    @mock.patch.object(subject, "discover_commit_contribution_repositories")
    @mock.patch.object(subject, "search_authored_pull_requests", return_value=[])
    def test_discovers_contribution_graph_only_repository(
        self, _authored_search, graph_search, _official_stats, _default_count
    ):
        text_to_cad = repository("earthtojake/text-to-cad")
        evidence_url = (
            "https://github.com/rudycelekli?tab=overview"
            "&from=2026-10-01&to=2026-10-31"
        )
        graph_search.return_value = {
            "earthtojake/text-to-cad": {
                "repository": text_to_cad,
                "contribution_graph_commits": 1,
                "contribution_graph_evidence_url": evidence_url,
                "contribution_graph_latest_at": "2026-10-08T07:00:00Z",
            },
            "company/internal": {
                "repository": repository(
                    "company/internal", visibility="INTERNAL"
                ),
                "contribution_graph_commits": 10,
                "contribution_graph_evidence_url": evidence_url,
                "contribution_graph_latest_at": "2026-10-08T07:00:00Z",
            },
        }

        discovered = subject.discover_repositories("token")

        self.assertEqual(len(discovered), 1)
        contribution = discovered[0]
        self.assertEqual(contribution["full_name"], "earthtojake/text-to-cad")
        self.assertEqual(
            contribution["verification_tier"],
            "github_contribution_graph_verified",
        )
        self.assertEqual(contribution["verification_label"], "GitHub graph verified")
        self.assertEqual(contribution["contributor_commits"], 1)
        self.assertEqual(contribution["merged_prs"], 0)
        self.assertEqual(contribution["accepted_additions"], 0)
        self.assertEqual(contribution["accepted_deletions"], 0)
        self.assertEqual(contribution["accepted_changed_files"], 0)
        self.assertEqual(
            contribution["contribution_graph_evidence_url"], evidence_url
        )

    @mock.patch.object(subject, "graphql_data")
    def test_contribution_graph_discovery_aggregates_daily_commit_counts(
        self, graphql_data
    ):
        text_to_cad = repository("earthtojake/text-to-cad")
        graphql_data.return_value = {
            "user": {
                "contributionsCollection": {
                    "commitContributionsByRepository": [
                        {
                            "repository": text_to_cad,
                            "contributions": {
                                "totalCount": 2,
                                "pageInfo": {"hasNextPage": False},
                                "nodes": [
                                    {
                                        "commitCount": 2,
                                        "occurredAt": "2026-10-07T07:00:00Z",
                                        "url": "https://github.com/rudycelekli?from=2026-10-01",
                                    },
                                    {
                                        "commitCount": 3,
                                        "occurredAt": "2026-10-08T07:00:00Z",
                                        "url": "https://github.com/rudycelekli?from=2026-10-01",
                                    },
                                ],
                            },
                        }
                    ]
                }
            }
        }

        discovered = subject.discover_commit_contribution_repositories(
            "token", subject.dt.date(2026, 1, 1), subject.dt.date(2026, 12, 31)
        )

        self.assertEqual(
            discovered["earthtojake/text-to-cad"]["contribution_graph_commits"],
            5,
        )
        self.assertEqual(
            discovered["earthtojake/text-to-cad"]["contribution_graph_latest_at"],
            "2026-10-08T07:00:00Z",
        )

    @mock.patch.object(subject, "graphql_data")
    def test_high_single_day_commit_count_is_not_mistaken_for_pagination(
        self, graphql_data
    ):
        busy = repository("upstream/busy-day")
        graphql_data.return_value = {
            "user": {
                "contributionsCollection": {
                    "commitContributionsByRepository": [
                        {
                            "repository": busy,
                            "contributions": {
                                "totalCount": 132,
                                "pageInfo": {"hasNextPage": False},
                                "nodes": [
                                    {
                                        "commitCount": 132,
                                        "occurredAt": "2026-10-05T07:00:00Z",
                                        "url": "https://github.com/rudycelekli?from=2026-10-01",
                                    }
                                ],
                            },
                        }
                    ]
                }
            }
        }

        discovered = subject.discover_commit_contribution_repositories(
            "token", subject.dt.date(2026, 10, 5), subject.dt.date(2026, 10, 5)
        )

        self.assertEqual(
            discovered["upstream/busy-day"]["contribution_graph_commits"], 132
        )
        graphql_data.assert_called_once()

    def test_svg_expands_for_new_projects_and_labels_reach(self):
        repositories = []
        for index in range(3):
            repositories.append(
                {
                    "name": f"Project {index + 1}",
                    "description": "A concise project description",
                    "accent": "#2DE2C5",
                    "secondary": "#4D7CFE",
                    "merged_prs": index + 1,
                    "contributor_commits": index + 2,
                    "accepted_additions": 100,
                    "accepted_deletions": 10,
                    "accepted_changed_files": 8,
                    "stargazers": 1_000 * (index + 1),
                    "forks": 100 * (index + 1),
                }
            )

        svg = subject.render_svg(repositories, "2026-09-28")

        self.assertIn('height="706"', svg)
        self.assertIn("3 verified projects", svg)
        self.assertIn("330 accepted line changes", svg)
        self.assertIn("6.0k combined stars", svg)
        self.assertIn("STARS", svg)
        self.assertIn("attributed commits", svg)
        self.assertIn("OPEN-SOURCE IMPACT, VERIFIED", svg)
        self.assertIn('clip-path="url(#contribution-card-0)"', svg)
        self.assertIn("every 30 min", svg)
        self.assertIn('transform="translate(0 24)"', svg)
        self.assertIn("Repository stars and forks describe project reach", svg)

    def test_readme_contribution_proof_is_rendered_as_a_comparable_table(self):
        repository = {
            "name": "Upstream Project",
            "url": "https://github.com/upstream/project",
            "contributors_url": "https://github.com/upstream/project/graphs/contributors",
            "commits_url": "https://github.com/upstream/project/commits?author=rudycelekli",
            "pull_requests_url": "https://github.com/upstream/project/pulls?q=author%3Arudycelekli+is%3Amerged",
            "verification_tier": "github_listed_contributor",
            "verification_label": "GitHub-listed contributor",
            "contributor_commits": 12,
            "merged_prs": 9,
            "accepted_additions": 1_234,
            "accepted_deletions": 56,
            "accepted_changed_files": 42,
            "stargazers": 7_890,
            "forks": 321,
        }

        section = subject.render_readme_section([repository], [], "2026-10-05")

        self.assertIn(
            "| Project and evidence | Attributed commits | Merged PRs | Accepted lines (+ / −) | Files | Repository reach |",
            section,
        )
        self.assertIn("[GitHub-listed contributor]", section)
        self.assertIn("| 12 | [9]", section)
        self.assertIn("| +1,234 / −56 | 42 | 7,890 ★ · 321 forks |", section)
        self.assertNotIn("- **[Upstream Project]", section)

        default_only = {
            **repository,
            "name": "Default Branch Project",
            "verification_tier": "default_branch_commit_verified",
            "verification_label": "Default-branch commit verified",
            "contributor_commits": 4,
            "merged_prs": 0,
            "accepted_additions": 0,
            "accepted_deletions": 0,
            "accepted_changed_files": 0,
        }
        default_section = subject.render_readme_section(
            [default_only], [], "2026-10-05"
        )
        self.assertIn("[Default-branch commit verified]", default_section)
        self.assertIn("| 4 | [0]", default_section)
        self.assertIn("| — | — | 7,890 ★ · 321 forks |", default_section)

        graph_only = {
            **repository,
            "name": "Graph Project",
            "verification_tier": "github_contribution_graph_verified",
            "verification_label": "GitHub graph verified",
            "contribution_graph_evidence_url": "https://github.com/rudycelekli?tab=overview",
            "contributor_commits": 1,
            "merged_prs": 0,
            "accepted_additions": 0,
            "accepted_deletions": 0,
            "accepted_changed_files": 0,
        }
        graph_section = subject.render_readme_section(
            [graph_only], [], "2026-10-05"
        )
        self.assertIn(
            "[GitHub graph verified](https://github.com/rudycelekli?tab=overview)",
            graph_section,
        )
        self.assertIn("| 1 | [0]", graph_section)
        self.assertIn("Only merged PRs contribute accepted-line", graph_section)

    @mock.patch.object(subject, "official_contribution_stats")
    @mock.patch.object(subject, "rest_json")
    def test_discovers_owned_public_projects_without_confusing_them_with_upstream(
        self, rest_json, official_stats
    ):
        owned_payload = [
            owned_repository(f"{subject.LOGIN}/testlore", stargazers_count=1),
            owned_repository(f"{subject.LOGIN}/proofseal", stargazers_count=2),
            owned_repository(f"{subject.LOGIN}/gradia-guard"),
            owned_repository(f"{subject.LOGIN}/{subject.LOGIN}"),
            owned_repository(f"{subject.LOGIN}/DreamMachine"),
            owned_repository(f"{subject.LOGIN}/forked", fork=True),
            owned_repository(f"{subject.LOGIN}/archived", archived=True),
        ]
        rest_json.side_effect = lambda _token, url: (
            [] if "/releases?" in url else owned_payload
        )
        official_stats.side_effect = lambda _token, full_name: {
            f"{subject.LOGIN}/testlore": (40, 1),
            f"{subject.LOGIN}/proofseal": (25, 1),
            f"{subject.LOGIN}/gradia-guard": (60, 1),
        }[full_name]

        discovered = subject.discover_owned_repositories("token")

        self.assertEqual(
            [item["name"] for item in discovered],
            ["ProofSeal", "TestLore", "Gradia Guard"],
        )
        self.assertTrue(all(item["ownership"] == "owner" for item in discovered))
        self.assertTrue(all(item["visibility"] == "public" for item in discovered))
        self.assertNotIn("DreamMachine", [item["name"] for item in discovered])

    @mock.patch.object(subject, "rest_json")
    def test_discovers_newest_public_prerelease(self, rest_json):
        rest_json.return_value = [
            {"draft": True, "tag_name": "v0.2.0-draft"},
            {
                "draft": False,
                "prerelease": True,
                "tag_name": "v0.1.0",
                "name": "Kin 0.1.0",
                "html_url": "https://github.com/rudycelekli/kin-connect/releases/tag/v0.1.0",
                "published_at": "2026-10-07T10:59:00Z",
            },
        ]

        release = subject.discover_latest_release(
            "token", "rudycelekli/kin-connect"
        )

        self.assertEqual(release["tag_name"], "v0.1.0")
        self.assertTrue(release["prerelease"])
        self.assertIn("/releases/tag/v0.1.0", release["url"])

    def test_owned_project_visual_is_compact_and_overflow_safe(self):
        projects = []
        for index in range(4):
            projects.append(
                {
                    "name": "testlore" if index == 0 else f"owned-project-{index}",
                    "description": "x" * 180,
                    "accent": "#2DE2C5",
                    "secondary": "#4D7CFE",
                    "contributor_commits": index + 1,
                    "stargazers": index,
                    "forks": 0,
                    "pushed_at": "2026-09-30T00:00:00Z",
                }
            )

        svg = subject.render_owned_svg(projects, 12, "2026-09-30")

        self.assertIn('height="518"', svg)
        self.assertIn("PUBLIC PROJECTS, OWNED", svg)
        self.assertIn("12 qualifying owned repos", svg)
        self.assertIn("4 spotlighted", svg)
        self.assertIn("OWNER", svg)
        self.assertIn("testlore", svg)
        self.assertIn('clip-path="url(#owned-card-0)"', svg)
        self.assertIn("prefers-reduced-motion", svg)

    def test_long_unbroken_copy_is_clamped(self):
        lines = subject.wrap_svg_text("x" * 200, width=20, lines=2)
        self.assertEqual(len(lines), 2)
        self.assertLessEqual(max(map(len, lines)), 20)
        self.assertTrue(lines[-1].endswith("…"))

    def test_profile_walkthrough_uses_live_evidence_and_reduced_motion(self):
        repositories = [
            {
                "merged_prs": 9,
                "accepted_additions": 1_234,
                "accepted_deletions": 56,
            },
            {
                "merged_prs": 4,
                "accepted_additions": 500,
                "accepted_deletions": 10,
            },
        ]
        profile = {
            "agentic_power_x": {"base": 42.5},
            "updated_at_utc": "2026-10-06",
        }

        svg = subject.render_profile_walkthrough_svg(
            repositories, [{}, {}, {}], profile
        )

        self.assertIn("RUDY CELEKLI / OPERATING BRIEF", svg)
        self.assertIn("07 CHAPTERS · 35 SEC", svg)
        self.assertIn("2 verified upstream projects", svg)
        self.assertIn("13 merged pull requests", svg)
        self.assertIn("1,800 accepted line changes", svg)
        self.assertIn("42.5 times", svg)
        self.assertIn("3 owned public projects", svg)
        self.assertIn("prefers-reduced-motion", svg)
        self.assertIn('class="scene-static"', svg)
        self.assertIn('class="walk-scan"', svg)
        self.assertIn('class="chapter-fill"', svg)
        self.assertIn("translateY(-10px)", svg)

    def test_hero_leads_with_positioning_and_live_machine_counted_proof(self):
        repositories = [
            {
                "merged_prs": 12,
                "contributor_commits": 110,
                "accepted_additions": 2_000,
                "accepted_deletions": 100,
                "stargazers": 900,
            },
            {
                "merged_prs": 5,
                "contributor_commits": 15,
                "accepted_additions": 400,
                "accepted_deletions": 25,
                "stargazers": 100,
            },
        ]
        profile = {
            "agentic_power_x": {"base": 51.2},
            "updated_at_utc": "2026-10-06",
        }

        svg = subject.render_hero_svg(repositories, profile)

        self.assertIn("EVIDENCE-FIRST AGENTIC SYSTEMS", svg)
        self.assertIn("Forward deployed AI researcher", svg)
        self.assertIn("2 VERIFIED PROJECTS", svg)
        self.assertIn("17 MERGED PRS", svg)
        self.assertIn("125 ATTRIBUTED COMMITS", svg)
        self.assertIn("2,525 ACCEPTED LINES", svg)
        self.assertIn("1.0k COMBINED STARS", svg)
        self.assertIn("51.2× PROVISIONAL AP", svg)
        self.assertIn("MACHINE-COUNTED · 2026-10-06 UTC", svg)
        self.assertIn("prefers-reduced-motion", svg)
        self.assertIn('class="packet"', svg)
        self.assertIn('class="ring-wave"', svg)
        self.assertIn('class="state-chip state-chip-4"', svg)
        self.assertIn('class="border-trace"', svg)
        self.assertNotIn('fill="url(#hero-accent)" font-family', svg)

    def test_top_contributor_proof_includes_only_observed_top_five_ranks(self):
        repositories = [
            {
                "name": "Top Project",
                "full_name": "upstream/top-project",
                "url": "https://github.com/upstream/top-project",
                "contributor_rank": 2,
                "stargazers": 12_345,
            },
            {
                "name": "Sixth Project",
                "full_name": "upstream/sixth-project",
                "url": "https://github.com/upstream/sixth-project",
                "contributor_rank": 6,
                "stargazers": 99_999,
            },
            {
                "name": "Pending Project",
                "full_name": "upstream/pending-project",
                "url": "https://github.com/upstream/pending-project",
                "contributor_rank": None,
                "stargazers": 500,
            },
        ]

        section = subject.render_top_contributor_readme(
            repositories, "2026-10-07"
        )

        self.assertIn("Top Project", section)
        self.assertIn("`#2`", section)
        self.assertIn("12k ★", section)
        self.assertNotIn("Sixth Project", section)
        self.assertNotIn("Pending Project", section)

    def test_building_now_re_ranks_public_projects_and_keeps_gradia_private(self):
        projects = [
            {
                "name": "Fresh Project",
                "full_name": "rudycelekli/fresh-project",
                "url": "https://github.com/rudycelekli/fresh-project",
                "description": "A freshly shipped public project",
                "homepage": "https://example.com/fresh",
                "topics": ["agents", "evaluation"],
                "stargazers": 1,
                "forks": 0,
                "contributor_commits": 80,
                "pushed_at": "2026-10-06T00:00:00Z",
                "accent": "#2DE2C5",
            },
            {
                "name": "Adopted Project",
                "full_name": "rudycelekli/adopted-project",
                "url": "https://github.com/rudycelekli/adopted-project",
                "description": "A broadly adopted public project",
                "homepage": None,
                "topics": [],
                "stargazers": 5,
                "forks": 1,
                "contributor_commits": 20,
                "pushed_at": "2026-06-01T00:00:00Z",
                "accent": "#9B7CFF",
            },
            {
                "name": "Small Project",
                "full_name": "rudycelekli/small-project",
                "url": "https://github.com/rudycelekli/small-project",
                "description": "A smaller public project",
                "homepage": None,
                "topics": [],
                "stargazers": 0,
                "forks": 0,
                "contributor_commits": 3,
                "pushed_at": "2026-10-05T00:00:00Z",
                "latest_release": {
                    "tag_name": "v0.1.0",
                    "name": "First public release",
                    "url": "https://github.com/rudycelekli/small-project/releases/tag/v0.1.0",
                    "published_at": "2026-10-07T00:00:00Z",
                    "prerelease": True,
                },
                "accent": "#F2A93B",
            },
        ]
        for index in range(3):
            projects.append(
                {
                    "name": f"Additional Project {index + 1}",
                    "full_name": f"rudycelekli/additional-project-{index + 1}",
                    "url": f"https://github.com/rudycelekli/additional-project-{index + 1}",
                    "description": "Another public project in the rotating frontier",
                    "homepage": None,
                    "topics": [],
                    "stargazers": 0,
                    "forks": 0,
                    "contributor_commits": 1,
                    "pushed_at": f"2026-09-0{index + 1}T00:00:00Z",
                    "accent": "#4D7CFE",
                }
            )

        featured = subject.select_featured_owned_projects(
            projects, "2026-10-07", limit=2
        )
        svg = subject.render_building_now_svg(projects, "2026-10-07")
        readme = subject.render_building_now_readme(projects, "2026-10-07")

        self.assertEqual(
            [repo["name"] for repo in featured],
            ["Adopted Project", "Small Project"],
        )
        self.assertIn("PRIVATE PRODUCT · PUBLIC EVIDENCE", svg)
        self.assertIn("PUBLIC FRONTIER / 6 AUTO-RANKED", svg)
        self.assertIn("VIEW 1 / 2", svg)
        self.assertIn("VIEW 2 / 2", svg)
        self.assertIn("frontier-page-two 16s", svg)
        self.assertIn("ROTATES EVERY 8 SEC", svg)
        self.assertIn("RE-RANKED EVERY 30 MIN", svg)
        self.assertIn("v0.1.0 PRE-RELEASE", svg)
        self.assertIn("prefers-reduced-motion", svg)
        self.assertIn('class="private-orbit"', svg)
        self.assertIn('class="frontier-progress"', svg)
        self.assertIn('class="frontier-row frontier-row-1"', svg)
        self.assertIn("Gradia", readme)
        self.assertIn("private repository evidence remains private", readme)
        self.assertIn("Adopted Project", readme)
        self.assertIn("v0.1.0 pre-release", readme)
        self.assertIn("Additional Project", readme)

    def test_profile_leads_with_upstream_proof_and_runs_twice_hourly(self):
        readme = (subject.ROOT / "README.md").read_text()
        workflow = (
            subject.ROOT / ".github" / "workflows" / "refresh-contribution-stats.yml"
        ).read_text()
        self.assertLess(
            readme.index("<!-- contribution-stats:start -->"),
            readme.index("## The proof stack"),
        )
        self.assertLess(
            readme.index("### Public projects I own"),
            readme.index("## The proof stack"),
        )
        self.assertIn('cron: "17,47 * * * *"', workflow)

    def test_professional_identity_and_community_links_are_visible(self):
        readme = (subject.ROOT / "README.md").read_text()
        self.assertIn("Forward Deployed AI Researcher", readme)
        self.assertIn("https://www.linkedin.com/in/rudymizrahi/", readme)
        self.assertIn("https://www.gradiahq.com", readme)
        self.assertIn("https://snorkel.ai/", readme)
        self.assertIn("https://axiomconsulting.ai/", readme)
        self.assertIn("https://charlotte.aitinkerers.org/", readme)
        self.assertIn("https://agentics.org/leadership/", readme)
        self.assertIn("./assets/profile-walkthrough.svg", readme)
        self.assertIn("./assets/building-now.svg", readme)
        self.assertIn("RuVector’s vector walkthrough", readme)
        self.assertLess(readme.index("LINKEDIN-connect"), readme.index("## Intelligence"))


if __name__ == "__main__":
    unittest.main()
