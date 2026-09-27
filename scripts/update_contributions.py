#!/usr/bin/env python3
"""Refresh public contribution evidence in the profile README and SVG."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DATA_FILE = ROOT / "data" / "contributions.json"
SVG_FILE = ROOT / "assets" / "open-source-contributions.svg"
LOGIN = "rudycelekli"
REPOSITORIES = (
    {
        "name": "Ruflo",
        "full_name": "ruvnet/ruflo",
        "accent": "#2DE2C5",
        "secondary": "#4D7CFE",
        "description": "Agent orchestration & autonomous workflows",
    },
    {
        "name": "Agentic-QE",
        "full_name": "proffesor-for-testing/agentic-qe",
        "accent": "#9B7CFF",
        "secondary": "#F2A93B",
        "description": "Agentic quality engineering infrastructure",
    },
)

QUERY = """
query($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        state
        merged
        additions
        deletions
        changedFiles
        url
      }
    }
  }
}
"""


def graphql(token: str, variables: dict[str, object]) -> dict[str, object]:
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "rudycelekli-profile-stats",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"GitHub GraphQL failed ({error.code}): {detail}") from error
    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
    return payload["data"]["search"]


def collect_repository(token: str, repository: dict[str, str]) -> dict[str, object]:
    query = f"repo:{repository['full_name']} is:pr author:{LOGIN}"
    cursor = None
    pull_requests: list[dict[str, object]] = []
    expected_total = 0

    while True:
        result = graphql(token, {"query": query, "cursor": cursor})
        expected_total = int(result["issueCount"])
        pull_requests.extend(node for node in result["nodes"] if node)
        page = result["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    if len(pull_requests) != expected_total:
        raise RuntimeError(
            f"Expected {expected_total} PRs for {repository['full_name']}, "
            f"received {len(pull_requests)}"
        )

    merged = [item for item in pull_requests if item["merged"]]
    opened = [item for item in pull_requests if item["state"] == "OPEN"]
    return {
        **repository,
        "url": f"https://github.com/{repository['full_name']}",
        "pull_requests_url": (
            f"https://github.com/{repository['full_name']}/pulls"
            f"?q=is%3Apr+author%3A{LOGIN}"
        ),
        "authored_prs": len(pull_requests),
        "merged_prs": len(merged),
        "open_prs": len(opened),
        "closed_unmerged_prs": len(pull_requests) - len(merged) - len(opened),
        "accepted_additions": sum(int(item["additions"]) for item in merged),
        "accepted_deletions": sum(int(item["deletions"]) for item in merged),
        "accepted_changed_files": sum(int(item["changedFiles"]) for item in merged),
    }


def compact(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{round(value / 1_000):.0f}k"


def metric(x: int, label: str, value: str, color: str) -> str:
    return f"""
      <g transform="translate({x} 0)">
        <text y="0" fill="{color}" font-size="31" font-weight="750">{html.escape(value)}</text>
        <text y="27" fill="#91A2B8" font-size="12" letter-spacing="1.2">{html.escape(label.upper())}</text>
      </g>"""


def render_svg(repositories: list[dict[str, object]], updated: str) -> str:
    cards: list[str] = []
    for index, repo in enumerate(repositories):
        x = 42 + index * 567
        merged = int(repo["merged_prs"])
        opened = int(repo["open_prs"])
        authored = int(repo["authored_prs"])
        added = int(repo["accepted_additions"])
        deleted = int(repo["accepted_deletions"])
        files = int(repo["accepted_changed_files"])
        cards.append(
            f"""
    <g transform="translate({x} 112)">
      <rect width="550" height="226" rx="22" fill="#0B1728" stroke="{repo['accent']}" stroke-opacity="0.42"/>
      <rect x="1" y="1" width="7" height="224" rx="4" fill="{repo['accent']}"/>
      <circle cx="39" cy="39" r="8" fill="{repo['accent']}"/>
      <text x="59" y="47" fill="#F8FAFC" font-size="24" font-weight="760">{html.escape(str(repo['name']))}</text>
      <text x="31" y="76" fill="#91A2B8" font-size="13">{html.escape(str(repo['description']))}</text>
      <g transform="translate(31 123)">
        {metric(0, 'merged PRs', str(merged), str(repo['accent']))}
        {metric(126, 'open PRs', str(opened), str(repo['secondary']))}
        {metric(244, 'authored', str(authored), '#F8FAFC')}
        {metric(372, 'files accepted', compact(files), '#F8FAFC')}
      </g>
      <line x1="31" y1="175" x2="519" y2="175" stroke="#203149"/>
      <text x="31" y="205" fill="#91A2B8" font-size="13">accepted code</text>
      <text x="132" y="205" fill="{repo['accent']}" font-size="15" font-weight="700">+{added:,}</text>
      <text x="220" y="205" fill="#F87171" font-size="15" font-weight="700">−{deleted:,}</text>
      <text x="519" y="205" fill="#65758B" font-size="12" text-anchor="end">merged PRs only</text>
    </g>"""
        )

    total_merged = sum(int(repo["merged_prs"]) for repo in repositories)
    total_open = sum(int(repo["open_prs"]) for repo in repositories)
    total_authored = sum(int(repo["authored_prs"]) for repo in repositories)
    total_lines = sum(
        int(repo["accepted_additions"]) + int(repo["accepted_deletions"])
        for repo in repositories
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="410" viewBox="0 0 1200 410" role="img" aria-labelledby="title desc">
  <title id="title">Open-source contribution evidence for Rudy Celekli</title>
  <desc id="desc">GitHub-verified contribution totals for Ruflo and Agentic-QE.</desc>
  <defs>
    <linearGradient id="canvas" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#07111E"/>
      <stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">
      <stop stop-color="#2DE2C5"/>
      <stop offset="0.52" stop-color="#4D7CFE"/>
      <stop offset="1" stop-color="#9B7CFF"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="410" rx="28" fill="url(#canvas)"/>
  <rect x="1" y="1" width="1198" height="408" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="32" width="66" height="4" rx="2" fill="url(#rule)"/>
  <text x="42" y="70" fill="#F8FAFC" font-family="Inter,Segoe UI,sans-serif" font-size="29" font-weight="780">OPEN-SOURCE CONTRIBUTOR</text>
  <text x="1158" y="68" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" text-anchor="end">GITHUB · {html.escape(updated)} UTC</text>
  <g font-family="Inter,Segoe UI,sans-serif">
    {''.join(cards)}
  </g>
  <g transform="translate(42 372)" font-family="ui-monospace,SFMono-Regular,monospace" font-size="13">
    <text fill="#2DE2C5">{total_merged} merged</text>
    <text x="108" fill="#65758B">•</text>
    <text x="130" fill="#9B7CFF">{total_open} open</text>
    <text x="220" fill="#65758B">•</text>
    <text x="242" fill="#F8FAFC">{total_authored} authored PRs</text>
    <text x="410" fill="#65758B">•</text>
    <text x="432" fill="#F2A93B">{total_lines:,} accepted line changes</text>
    <text x="1116" fill="#65758B" text-anchor="end">public GraphQL evidence · refreshed daily</text>
  </g>
</svg>
"""


def render_readme_section(repositories: list[dict[str, object]], updated: str) -> str:
    lines = [
        "<!-- contribution-stats:start -->",
        "## Open-source contributor",
        "",
        "I contribute upstream to agent orchestration and quality-engineering infrastructure. These numbers come directly from GitHub; **accepted-code totals include merged pull requests only**.",
        "",
        '<img src="./assets/open-source-contributions.svg" width="100%" alt="GitHub-verified contribution statistics for Ruflo and Agentic-QE" />',
        "",
    ]
    for repo in repositories:
        lines.append(
            f"- **[{repo['name']}]({repo['pull_requests_url']})** — "
            f"{int(repo['merged_prs']):,} merged / {int(repo['open_prs']):,} open / "
            f"{int(repo['authored_prs']):,} authored PRs; "
            f"+{int(repo['accepted_additions']):,} / −{int(repo['accepted_deletions']):,} "
            f"accepted lines across {int(repo['accepted_changed_files']):,} changed files."
        )
    lines.extend(
        [
            "",
            f"<sub>Last verified {updated} UTC · refreshed daily by [GitHub Actions](./.github/workflows/refresh-contribution-stats.yml) · [machine-readable evidence](./data/contributions.json)</sub>",
            "<!-- contribution-stats:end -->",
        ]
    )
    return "\n".join(lines)


def update_readme(section: str) -> None:
    current = README.read_text()
    pattern = re.compile(
        r"<!-- contribution-stats:start -->.*?<!-- contribution-stats:end -->",
        re.DOTALL,
    )
    updated, replacements = pattern.subn(section, current)
    if replacements != 1:
        raise RuntimeError(f"Expected one contribution section, found {replacements}")
    README.write_text(updated)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN is required", file=sys.stderr)
        return 2

    updated = dt.datetime.now(dt.timezone.utc).date().isoformat()
    repositories = [collect_repository(token, repo) for repo in REPOSITORIES]
    payload = {
        "schema_version": 1,
        "source": "GitHub GraphQL API",
        "login": LOGIN,
        "updated_at_utc": updated,
        "methodology": {
            "authored_prs": "Pull requests returned by is:pr author:<login>.",
            "accepted_code": "Additions, deletions, and changed files from merged pull requests only.",
            "open_prs": "Authored pull requests whose current state is OPEN.",
        },
        "repositories": repositories,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    SVG_FILE.write_text(render_svg(repositories, updated))
    update_readme(render_readme_section(repositories, updated))
    print(
        "Updated contribution evidence: "
        + ", ".join(
            f"{repo['name']}={repo['merged_prs']} merged/{repo['open_prs']} open"
            for repo in repositories
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
