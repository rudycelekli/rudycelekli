import unittest
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
    @mock.patch.object(subject, "official_contribution_count")
    @mock.patch.object(subject, "search_authored_pull_requests")
    def test_discovers_and_upgrades_each_accepted_evidence_tier(
        self, authored_search, official_count, default_count
    ):
        accepted = repository("upstream/new-project")
        pending = repository("upstream/pending-index")
        default_only = repository("upstream/default-only")
        unaccepted = repository("upstream/open-only")
        private = repository("company/private", isPrivate=True)
        owned = repository(f"{subject.LOGIN}/owned-project")
        authored_search.return_value = [
            pull_request(accepted, 6),
            pull_request(pending, 5),
            pull_request(default_only, 4, merged=False),
            pull_request(unaccepted, 3, merged=False),
            pull_request(private, 2),
            pull_request(owned, 1),
        ]
        official_count.side_effect = lambda _token, full_name: (
            7 if full_name == "upstream/new-project" else None
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
            [call.args[1] for call in official_count.call_args_list],
            [
                "upstream/new-project",
                "upstream/pending-index",
                "upstream/default-only",
            ],
        )

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

        self.assertIn('height="682"', svg)
        self.assertIn("3 verified projects", svg)
        self.assertIn("330 accepted line changes", svg)
        self.assertIn("6.0k combined stars", svg)
        self.assertIn("STARS", svg)
        self.assertIn("attributed commits", svg)
        self.assertIn("OPEN-SOURCE IMPACT, VERIFIED", svg)
        self.assertIn('clip-path="url(#contribution-card-0)"', svg)
        self.assertIn("every 30 min", svg)
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

    @mock.patch.object(subject, "official_contribution_count")
    @mock.patch.object(subject, "rest_json")
    def test_discovers_owned_public_projects_without_confusing_them_with_upstream(
        self, rest_json, official_count
    ):
        rest_json.return_value = [
            owned_repository(f"{subject.LOGIN}/testlore", stargazers_count=1),
            owned_repository(f"{subject.LOGIN}/proofseal", stargazers_count=2),
            owned_repository(f"{subject.LOGIN}/gradia-guard"),
            owned_repository(f"{subject.LOGIN}/{subject.LOGIN}"),
            owned_repository(f"{subject.LOGIN}/DreamMachine"),
            owned_repository(f"{subject.LOGIN}/forked", fork=True),
            owned_repository(f"{subject.LOGIN}/archived", archived=True),
        ]
        official_count.side_effect = lambda _token, full_name: {
            f"{subject.LOGIN}/testlore": 40,
            f"{subject.LOGIN}/proofseal": 25,
            f"{subject.LOGIN}/gradia-guard": 60,
        }[full_name]

        discovered = subject.discover_owned_repositories("token")

        self.assertEqual(
            [item["name"] for item in discovered],
            ["ProofSeal", "TestLore", "Gradia Guard"],
        )
        self.assertTrue(all(item["ownership"] == "owner" for item in discovered))
        self.assertTrue(all(item["visibility"] == "public" for item in discovered))
        self.assertNotIn("DreamMachine", [item["name"] for item in discovered])

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
        self.assertLess(readme.index("LINKEDIN-connect"), readme.index("## Intelligence"))


if __name__ == "__main__":
    unittest.main()
