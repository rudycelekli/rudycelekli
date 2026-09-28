import unittest
from unittest import mock

from scripts import update_contributions as subject


def pull_request(repository, number=1):
    return {
        "number": number,
        "title": "fix: accepted upstream work",
        "createdAt": "2026-09-01T00:00:00Z",
        "mergedAt": "2026-09-02T00:00:00Z",
        "merged": True,
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

    @mock.patch.object(subject, "official_contribution_count")
    @mock.patch.object(subject, "graphql")
    def test_discovers_all_eligible_projects_without_a_fixed_allowlist(
        self, graphql, official_count
    ):
        accepted = repository("upstream/new-project")
        unlisted = repository("upstream/not-official")
        private = repository("company/private", isPrivate=True)
        owned = repository(f"{subject.LOGIN}/owned-project")
        graphql.return_value = {
            "issueCount": 4,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                pull_request(accepted, 4),
                pull_request(unlisted, 3),
                pull_request(private, 2),
                pull_request(owned, 1),
            ],
        }
        official_count.side_effect = lambda _token, full_name: (
            7 if full_name == "upstream/new-project" else None
        )

        discovered = subject.discover_repositories("token")

        self.assertEqual([item["full_name"] for item in discovered], ["upstream/new-project"])
        self.assertEqual(discovered[0]["merged_prs"], 1)
        self.assertEqual(discovered[0]["contributor_commits"], 7)
        self.assertEqual(discovered[0]["stargazers"], 1_234)
        self.assertEqual(discovered[0]["forks"], 56)
        self.assertEqual(
            [call.args[1] for call in official_count.call_args_list],
            ["upstream/new-project", "upstream/not-official"],
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
        self.assertIn("6.0k combined stars", svg)
        self.assertIn("STARS", svg)
        self.assertIn("Repository stars and forks describe project reach", svg)

    def test_long_unbroken_copy_is_clamped(self):
        lines = subject.wrap_svg_text("x" * 200, width=20, lines=2)
        self.assertEqual(len(lines), 2)
        self.assertLessEqual(max(map(len, lines)), 20)
        self.assertTrue(lines[-1].endswith("…"))


if __name__ == "__main__":
    unittest.main()
