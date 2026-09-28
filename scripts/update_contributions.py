#!/usr/bin/env python3
"""Refresh public contribution evidence in the profile README and SVG."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
DATA_FILE = ROOT / "data" / "contributions.json"
SVG_FILE = ROOT / "assets" / "open-source-contributions.svg"
AP_DATA_FILE = ROOT / "data" / "agentic-power.json"
AP_SVG_FILE = ROOT / "assets" / "agentic-power-profile.svg"
REPOSITORY_DATA_FILE = ROOT / "data" / "repository-work-aggregate.json"
LOGIN = "rudycelekli"
PERIOD_START = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
OPERATOR_DIRECTION_HOURS_PER_WEEK = {
    "low": 1.5,
    "base": 1.75,
    "high": 2.0,
}
# Cosmetic copy/color overrides only. Inclusion is always discovered from GitHub.
PRESENTATION_OVERRIDES = {
    "ruvnet/ruflo": {
        "name": "Ruflo",
        "accent": "#2DE2C5",
        "secondary": "#4D7CFE",
        "description": "Agent orchestration & autonomous workflows",
    },
    "proffesor-for-testing/agentic-qe": {
        "name": "Agentic-QE",
        "accent": "#9B7CFF",
        "secondary": "#F2A93B",
        "description": "Agentic quality engineering infrastructure",
    },
}
ACCENT_PAIRS = (
    ("#2DE2C5", "#4D7CFE"),
    ("#9B7CFF", "#F2A93B"),
    ("#F2A93B", "#2DE2C5"),
    ("#4D7CFE", "#9B7CFF"),
)

DISCOVERY_QUERY = """
query($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        createdAt
        mergedAt
        merged
        additions
        deletions
        changedFiles
        url
        commits { totalCount }
        comments { totalCount }
        reviews { totalCount }
        repository {
          name
          nameWithOwner
          description
          url
          isPrivate
          isFork
          stargazerCount
          forkCount
          owner { login }
        }
      }
    }
  }
}
"""


def graphql(token: str, variables: dict[str, object]) -> dict[str, object]:
    body = json.dumps({"query": DISCOVERY_QUERY, "variables": variables}).encode()
    for attempt in range(6):
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
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if error.code >= 500 and attempt < 5:
                time.sleep(min(2**attempt, 16))
                continue
            if error.code == 403 and attempt < 5:
                reset_at = int(error.headers.get("X-RateLimit-Reset", "0"))
                delay = max(reset_at - int(time.time()) + 2, 5)
                time.sleep(min(delay, 60))
                continue
            raise RuntimeError(
                f"GitHub GraphQL failed ({error.code}): {detail}"
            ) from error
        except urllib.error.URLError as error:
            if attempt < 5:
                time.sleep(min(2**attempt, 16))
                continue
            raise RuntimeError(f"GitHub GraphQL network failure: {error}") from error
        if payload.get("errors"):
            raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
        return payload["data"]["search"]
    raise RuntimeError("GitHub GraphQL failed after retries")


def official_contribution_count(token: str, full_name: str) -> int | None:
    """Return GitHub's commit count only when the login is in Contributors."""
    page = 1
    while True:
        contributors = rest_json(
            token,
            f"https://api.github.com/repos/{full_name}/contributors"
            f"?per_page=100&page={page}",
        )
        if not isinstance(contributors, list):
            raise RuntimeError(
                f"Unexpected GitHub Contributors response for {full_name}"
            )

        for contributor in contributors:
            if contributor.get("login", "").casefold() == LOGIN.casefold():
                return int(contributor["contributions"])
        if len(contributors) < 100:
            return None
        page += 1


def rest_json(token: str, url: str) -> object:
    for attempt in range(8):
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "rudycelekli-profile-stats",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if (
                error.code == 403
                and "secondary rate limit" in detail.casefold()
                and attempt < 7
            ):
                time.sleep(min(30 * (attempt + 1), 60))
                continue
            if (
                error.code == 403
                and "rate limit exceeded" in detail.casefold()
                and attempt < 7
            ):
                reset_at = int(error.headers.get("X-RateLimit-Reset", "0"))
                delay = max(reset_at - int(time.time()) + 2, 10)
                time.sleep(min(delay, 60))
                continue
            if error.code >= 500 and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(
                f"GitHub REST API failed ({error.code}): {detail}"
            ) from error
    raise RuntimeError("GitHub REST API failed after retries")


def search_commits_window(
    token: str, start: dt.date, end: dt.date
) -> list[dict[str, object]]:
    """Return every accessible default-branch commit in a date window."""
    query = f"author:{LOGIN} author-date:{start.isoformat()}..{end.isoformat()}"

    def fetch(page: int) -> dict[str, object]:
        parameters = urllib.parse.urlencode(
            {
                "q": query,
                "per_page": 100,
                "page": page,
                "sort": "author-date",
                "order": "asc",
            }
        )
        for incomplete_attempt in range(6):
            payload = rest_json(
                token, f"https://api.github.com/search/commits?{parameters}"
            )
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected commit-search response")
            # Authenticated search has a much tighter secondary limit than core REST.
            time.sleep(6.2)
            if not payload.get("incomplete_results"):
                return payload
            time.sleep(min(10 * (incomplete_attempt + 1), 30))
        raise RuntimeError("GitHub commit search remained incomplete after retries")

    first = fetch(1)
    total = int(first["total_count"])
    if total > 500 and start != end:
        midpoint = start + (end - start) // 2
        return search_commits_window(token, start, midpoint) + search_commits_window(
            token, midpoint + dt.timedelta(days=1), end
        )
    if total > 1_000:
        if start == end:
            raise RuntimeError(
                f"More than 1,000 commits occurred on {start}; cannot exhaust GitHub search"
            )
        midpoint = start + (end - start) // 2
        return search_commits_window(token, start, midpoint) + search_commits_window(
            token, midpoint + dt.timedelta(days=1), end
        )

    items = list(first["items"])
    for page in range(2, math.ceil(total / 100) + 1):
        items.extend(fetch(page)["items"])
    if len(items) != total:
        raise RuntimeError(
            f"Expected {total} commit-search results, received {len(items)}"
        )
    return items


def collect_repository_aggregate(
    token: str, updated: str, excluded: set[str]
) -> dict[str, object]:
    """Collect a redacted aggregate since 2025 from accessible repositories."""
    category_rules = {
        "feature_and_research": {
            "prefixes": {
                "feat",
                "release",
                "research",
                "console",
                "api",
                "marketing",
                "site",
                "about",
                "welcome",
            },
            "heh_base": 2.5,
            "direction_base": 0.30,
        },
        "fix_and_refactor": {
            "prefixes": {"fix", "refactor", "security"},
            "heh_base": 1.5,
            "direction_base": 0.20,
        },
        "quality": {
            "prefixes": {"test", "tests", "ci", "build"},
            "heh_base": 1.0,
            "direction_base": 0.15,
        },
        "knowledge": {
            "prefixes": {"docs", "paper", "data"},
            "heh_base": 0.75,
            "direction_base": 0.12,
        },
        "maintenance": {
            "prefixes": {"chore", "sandbox"},
            "heh_base": 0.5,
            "direction_base": 0.08,
        },
    }
    prefix_pattern = re.compile(r"([A-Za-z0-9_-]+)(?:\([^)]*\))?[!:]")
    category_counts = {category: 0 for category in category_rules}
    repository_commit_counts: dict[str, int] = {}
    seen_commits: set[str] = set()
    heh_base = 0.0
    direction_without_setup = 0.0
    dates: list[str] = []
    monthly: dict[str, dict[str, object]] = {}

    updated_date = dt.date.fromisoformat(updated)
    cursor = dt.date(PERIOD_START.year, PERIOD_START.month, 1)
    while cursor <= updated_date:
        next_month = (
            dt.date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else dt.date(cursor.year, cursor.month + 1, 1)
        )
        window_end = min(next_month - dt.timedelta(days=1), updated_date)
        for commit in search_commits_window(token, cursor, window_end):
            full_name = str(commit["repository"]["full_name"])
            commit_key = str(commit["sha"])
            if (
                full_name.casefold() in excluded
                or bool(commit["repository"].get("fork"))
                or commit_key in seen_commits
                or len(commit.get("parents", [])) > 1
            ):
                continue
            seen_commits.add(commit_key)
            title = str(commit["commit"]["message"]).splitlines()[0]
            match = prefix_pattern.match(title)
            prefix = match.group(1).casefold() if match else "other"
            category = "maintenance"
            for candidate, rule in category_rules.items():
                if prefix in rule["prefixes"]:
                    category = candidate
                    break
            rule = category_rules[category]
            category_counts[category] += 1
            repository_commit_counts[full_name] = (
                repository_commit_counts.get(full_name, 0) + 1
            )
            heh_base += float(rule["heh_base"])
            direction_without_setup += float(rule["direction_base"])
            committed_at = str(commit["commit"]["author"]["date"])
            month = committed_at[:7]
            dates.append(committed_at)
            month_data = monthly.setdefault(
                month,
                {
                    "default_branch_non_merge_commits": 0,
                    "active_repository_keys": set(),
                    "category_counts": {
                        candidate: 0 for candidate in category_rules
                    },
                    "skilled_human_equivalent_hours": {
                        "low": 0.0,
                        "base": 0.0,
                        "high": 0.0,
                    },
                    "modeled_human_direction_hours_without_setup": {
                        "low": 0.0,
                        "base": 0.0,
                        "high": 0.0,
                    },
                },
            )
            month_data["default_branch_non_merge_commits"] += 1
            month_data["active_repository_keys"].add(full_name)
            month_data["category_counts"][category] += 1
            for key, multiplier in (("low", 0.70), ("base", 1.0), ("high", 1.40)):
                month_data["skilled_human_equivalent_hours"][key] += float(
                    rule["heh_base"]
                ) * multiplier
            for key, multiplier in (("low", 0.70), ("base", 1.0), ("high", 1.50)):
                month_data["modeled_human_direction_hours_without_setup"][key] += float(
                    rule["direction_base"]
                ) * multiplier
        cursor = next_month

    repository_months = sum(
        len(month_data["active_repository_keys"]) for month_data in monthly.values()
    )
    setup_base = 2.0 * repository_months
    direction_base = direction_without_setup + setup_base
    published_monthly: dict[str, dict[str, object]] = {}
    for month, month_data in sorted(monthly.items()):
        active_repositories = len(month_data.pop("active_repository_keys"))
        without_setup = month_data.pop(
            "modeled_human_direction_hours_without_setup"
        )
        setup = {
            "low": 1.0 * active_repositories,
            "base": 2.0 * active_repositories,
            "high": 4.0 * active_repositories,
        }
        month_data["active_repositories"] = active_repositories
        month_data["modeled_setup_hours"] = setup
        month_data["modeled_human_direction_hours"] = {
            key: round(float(without_setup[key]) + setup[key], 2)
            for key in ("low", "base", "high")
        }
        month_data["skilled_human_equivalent_hours"] = {
            key: round(
                float(month_data["skilled_human_equivalent_hours"][key]), 2
            )
            for key in ("low", "base", "high")
        }
        published_monthly[month] = month_data

    return {
        "schema_version": 1,
        "updated_at_utc": updated,
        "privacy": "Repository names, commit titles, URLs, and code are intentionally excluded.",
        "scope": "Authored work since 2025 in accessible, non-fork default branches",
        "repository_count": len(repository_commit_counts),
        "default_branch_non_merge_commits": sum(repository_commit_counts.values()),
        "per_repository_commit_counts_unlabeled": sorted(
            repository_commit_counts.values(), reverse=True
        ),
        "category_counts": category_counts,
        "monthly": published_monthly,
        "window": {
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        },
        "acceptance_basis": "Non-merge commits reached a repository default branch; this is treated as repository-level acceptance.",
        "skilled_human_equivalent_hours": {
            "low": round(heh_base * 0.70, 2),
            "base": round(heh_base, 2),
            "high": round(heh_base * 1.40, 2),
        },
        "modeled_human_direction_hours": {
            "low": round(direction_without_setup * 0.70 + setup_base * 0.50, 2),
            "base": round(direction_base, 2),
            "high": round(direction_without_setup * 1.50 + setup_base * 2.0, 2),
        },
        "methodology": {
            "collection": "Authenticated GitHub commit search across all currently accessible repositories. GitHub searches the default branch; results are exhaustively partitioned when a window exceeds 1,000 results.",
            "heh": "Conservative outcome-class scenarios from conventional commit categories; merge commits and duplicate SHAs are excluded to avoid duplicate work.",
            "direction": "Modeled active direction per outcome class plus two hours per active repository-month for setup, review, coordination, and maintenance.",
            "limitation": "Default-branch presence is a repository-level acceptance proxy, not a completed independent audit. No repository names, messages, links, code, employer, or client data are published.",
        },
    }


def readable_description(value: object) -> str:
    """Prefer an English summary when a bilingual GitHub description provides one."""
    description = " ".join(str(value or "Open-source software").split())
    if "。" in description:
        trailing = description.rsplit("。", 1)[-1].strip()
        if trailing:
            description = trailing
    return description


def search_merged_pull_requests(
    token: str, start: dt.date, end: dt.date
) -> list[dict[str, object]]:
    """Exhaust GitHub PR search, splitting date windows above its 1,000-result cap."""
    query = (
        f"is:pr author:{LOGIN} is:merged "
        f"merged:{start.isoformat()}..{end.isoformat()}"
    )
    cursor = None
    pull_requests: list[dict[str, object]] = []
    expected_total = 0

    while True:
        result = graphql(token, {"query": query, "cursor": cursor})
        expected_total = int(result["issueCount"])
        if expected_total > 1_000:
            if start == end:
                raise RuntimeError(
                    f"More than 1,000 merged PRs occurred on {start}; "
                    "GitHub search cannot exhaust this day"
                )
            midpoint = start + (end - start) // 2
            return search_merged_pull_requests(
                token, start, midpoint
            ) + search_merged_pull_requests(
                token, midpoint + dt.timedelta(days=1), end
            )
        pull_requests.extend(node for node in result["nodes"] if node)
        page = result["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    if len(pull_requests) != expected_total:
        raise RuntimeError(
            f"Expected {expected_total} merged PRs for {LOGIN}, "
            f"received {len(pull_requests)}"
        )
    return pull_requests


def discover_repositories(token: str) -> list[dict[str, object]]:
    """Discover every public upstream repo with a merged PR and contributor proof."""
    pull_requests = search_merged_pull_requests(
        token, dt.date(2008, 1, 1), dt.datetime.now(dt.timezone.utc).date()
    )

    grouped: dict[str, dict[str, object]] = {}
    for pull_request in pull_requests:
        repository = pull_request.get("repository") or {}
        full_name = str(repository.get("nameWithOwner", ""))
        owner = repository.get("owner") or {}
        if (
            not full_name
            or bool(repository.get("isPrivate"))
            or bool(repository.get("isFork"))
            or str(owner.get("login", "")).casefold() == LOGIN.casefold()
        ):
            continue
        entry = grouped.setdefault(
            full_name,
            {"repository": repository, "pull_requests": []},
        )
        entry["pull_requests"].append(pull_request)

    repositories: list[dict[str, object]] = []
    for full_name, entry in grouped.items():
        contributor_commits = official_contribution_count(token, full_name)
        if contributor_commits is None:
            continue
        repository = entry["repository"]
        merged = entry["pull_requests"]
        override = PRESENTATION_OVERRIDES.get(full_name, {})
        palette_index = hashlib.sha256(full_name.encode()).digest()[0] % len(
            ACCENT_PAIRS
        )
        accent, secondary = ACCENT_PAIRS[palette_index]
        repositories.append(
            {
                "name": override.get("name", repository["name"]),
                "full_name": full_name,
                "accent": override.get("accent", accent),
                "secondary": override.get("secondary", secondary),
                "description": override.get(
                    "description", readable_description(repository.get("description"))
                ),
                "url": repository["url"],
                "contributors_url": f"https://github.com/{full_name}/graphs/contributors",
                "pull_requests_url": (
                    f"https://github.com/{full_name}/pulls"
                    f"?q=is%3Apr+author%3A{LOGIN}+is%3Amerged"
                ),
                "official_contributor": True,
                "contributor_commits": contributor_commits,
                "merged_prs": len(merged),
                "accepted_additions": sum(int(item["additions"]) for item in merged),
                "accepted_deletions": sum(int(item["deletions"]) for item in merged),
                "accepted_changed_files": sum(
                    int(item["changedFiles"]) for item in merged
                ),
                "stargazers": int(repository["stargazerCount"]),
                "forks": int(repository["forkCount"]),
                "_merged_pull_requests": merged,
            }
        )

    repositories.sort(
        key=lambda item: (
            -int(item["merged_prs"]),
            -int(item["stargazers"]),
            str(item["full_name"]).casefold(),
        )
    )
    return repositories


def compact(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{round(value / 1_000):.0f}k"


def ellipsize(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip() + "…"


def wrap_svg_text(value: object, width: int = 64, lines: int = 2) -> list[str]:
    """Wrap predictable SVG copy and clamp it before it can escape a card."""
    words = str(value).split()
    wrapped: list[str] = []
    current = ""
    for word in words:
        while len(word) > width:
            if current:
                wrapped.append(current)
                current = ""
                if len(wrapped) == lines:
                    break
            wrapped.append(word[:width])
            word = word[width:]
            if len(wrapped) == lines:
                break
        if len(wrapped) == lines:
            break
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
        else:
            wrapped.append(current)
            current = word
            if len(wrapped) == lines:
                break
    if current and len(wrapped) < lines:
        wrapped.append(current)
    original = " ".join(words)
    visible = " ".join(wrapped)
    if len(visible) < len(original) and wrapped:
        wrapped[-1] = ellipsize(wrapped[-1], max(width - 1, 1))
        if not wrapped[-1].endswith("…"):
            wrapped[-1] += "…"
    return wrapped or [""]


def metric(x: int, label: str, value: str, color: str) -> str:
    return f"""
      <g transform="translate({x} 0)">
        <text y="0" fill="{color}" font-size="31" font-weight="750">{html.escape(value)}</text>
        <text y="27" fill="#91A2B8" font-size="12" letter-spacing="1.2">{html.escape(label.upper())}</text>
      </g>"""


def estimate_work_item(repo: dict[str, object], pull_request: dict[str, object]) -> dict[str, object]:
    """Build a transparent modeled AP work item from one accepted pull request."""
    title = str(pull_request["title"])
    lowered = title.casefold()
    if any(
        keyword in lowered
        for keyword in (
            "security",
            "approval",
            "quality",
            "validation",
            "coherence",
            "coordination",
            "workflow",
            "graph",
            "brain",
            "learning",
            "rvf",
            "memory",
        )
    ):
        category = "systems_engineering"
        outcome_base_hours = 5.5
    elif any(
        keyword in lowered
        for keyword in ("ci", "test", "performance", "metrics", "deps", "docs")
    ):
        category = "reliability_and_qe"
        outcome_base_hours = 3.5
    else:
        category = "software_engineering"
        outcome_base_hours = 4.5

    additions = int(pull_request["additions"])
    deletions = int(pull_request["deletions"])
    changed_files = int(pull_request["changedFiles"])
    commits = int(pull_request["commits"]["totalCount"])
    comments = int(pull_request["comments"]["totalCount"])
    reviews = int(pull_request["reviews"]["totalCount"])
    churn = additions + deletions

    # HEH uses the accepted outcome type plus inspected complexity signals. It is
    # not a conversion from lines of code to hours.
    heh_base = (
        outcome_base_hours
        + 0.45 * math.sqrt(churn)
        + 0.35 * min(changed_files, 20)
        + 0.5 * max(commits - 1, 0)
    )
    # Direction is not observed. This model assigns active briefing, review,
    # correction, and coordination time from visible work/review events only.
    direction_base = (
        0.35
        + 0.12 * min(commits, 8)
        + 0.07 * min(changed_files, 20)
        + 0.12 * comments
        + 0.18 * reviews
    )
    return {
        "project": repo["full_name"],
        "pull_request": int(pull_request["number"]),
        "title": title,
        "url": pull_request["url"],
        "accepted": True,
        "acceptance_basis": "merged pull request",
        "category": category,
        "created_at": pull_request["createdAt"],
        "merged_at": pull_request["mergedAt"],
        "evidence": {
            "additions": additions,
            "deletions": deletions,
            "changed_files": changed_files,
            "commits": commits,
            "comments": comments,
            "reviews": reviews,
        },
        "heh": {
            "low": round(heh_base * 0.75, 2),
            "base": round(heh_base, 2),
            "high": round(heh_base * 1.35, 2),
        },
        "modeled_human_direction_hours": {
            "low": round(direction_base * 0.70, 2),
            "base": round(direction_base, 2),
            "high": round(direction_base * 1.40, 2),
        },
    }


def calculate_agentic_power(
    repositories: list[dict[str, object]],
    updated: str,
    repository_aggregate: dict[str, object] | None,
) -> dict[str, object]:
    work_items = [
        estimate_work_item(repo, pull_request)
        for repo in repositories
        for pull_request in repo["_merged_pull_requests"]
        if str(pull_request["mergedAt"]) >= "2025-01-01T00:00:00Z"
    ]
    work_items.sort(key=lambda item: str(item["merged_at"]))

    updated_date = dt.date.fromisoformat(updated)
    months: list[str] = []
    cursor = dt.date(PERIOD_START.year, PERIOD_START.month, 1)
    while cursor <= updated_date:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (
            dt.date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else dt.date(cursor.year, cursor.month + 1, 1)
        )
    monthly: list[dict[str, object]] = []
    repository_monthly = (repository_aggregate or {}).get("monthly", {})
    for month in months:
        public_items = [
            item for item in work_items if str(item["merged_at"]).startswith(month)
        ]
        public_projects = len({str(item["project"]) for item in public_items})
        public_setup = {
            "low": 1.0 * public_projects,
            "base": 2.0 * public_projects,
            "high": 4.0 * public_projects,
        }
        repository_work = (
            repository_monthly.get(month, {})
            if isinstance(repository_monthly, dict)
            else {}
        )
        repository_heh = repository_work.get("skilled_human_equivalent_hours", {})
        repository_direction = repository_work.get(
            "modeled_human_direction_hours", {}
        )
        month_heh = {
            key: round(
                sum(float(item["heh"][key]) for item in public_items)
                + float(repository_heh.get(key, 0.0)),
                2,
            )
            for key in ("low", "base", "high")
        }
        month_proxy_direction = {
            key: round(
                sum(
                    float(item["modeled_human_direction_hours"][key])
                    for item in public_items
                )
                + public_setup[key]
                + float(repository_direction.get(key, 0.0)),
                2,
            )
            for key in ("low", "base", "high")
        }
        monthly.append(
            {
                "month": month,
                "public_merged_pull_requests": len(public_items),
                "repository_default_branch_commits": int(
                    repository_work.get("default_branch_non_merge_commits", 0)
                ),
                "active_repositories": int(
                    repository_work.get("active_repositories", 0)
                ),
                "skilled_human_equivalent_hours": month_heh,
                "uncalibrated_github_direction_proxy_hours": month_proxy_direction,
            }
        )

    heh = {
        key: round(
            sum(float(month["skilled_human_equivalent_hours"][key]) for month in monthly),
            2,
        )
        for key in ("low", "base", "high")
    }
    proxy_direction = {
        key: round(
            sum(
                float(month["uncalibrated_github_direction_proxy_hours"][key])
                for month in monthly
            ),
            2,
        )
        for key in ("low", "base", "high")
    }
    starts = [str(item["created_at"]) for item in work_items]
    ends = [str(item["merged_at"]) for item in work_items]
    if repository_aggregate and repository_aggregate.get("window"):
        if repository_aggregate["window"].get("start"):
            starts.append(str(repository_aggregate["window"]["start"]))
        if repository_aggregate["window"].get("end"):
            ends.append(str(repository_aggregate["window"]["end"]))

    def as_utc(value: str) -> dt.datetime:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )

    evidence_weeks = round(
        (as_utc(max(ends)) - as_utc(min(starts))).total_seconds() / 604_800,
        2,
    )
    operator_direction = {
        key: evidence_weeks * OPERATOR_DIRECTION_HOURS_PER_WEEK[key]
        for key in ("low", "base", "high")
    }
    direction_calibration = {
        key: (
            operator_direction[key] / proxy_direction["base"]
            if proxy_direction["base"]
            else 1.0
        )
        for key in ("low", "base", "high")
    }
    for month in monthly:
        proxy_share = (
            float(month["uncalibrated_github_direction_proxy_hours"]["base"])
            / proxy_direction["base"]
            if proxy_direction["base"]
            else 0.0
        )
        calibrated = {
            key: round(operator_direction[key] * proxy_share, 2)
            for key in ("low", "base", "high")
        }
        month["modeled_human_direction_hours"] = calibrated
        month_heh = month["skilled_human_equivalent_hours"]
        month["agentic_power_x"] = {
            "low": round(float(month_heh["low"]) / calibrated["high"], 1)
            if calibrated["high"]
            else 0.0,
            "base": round(float(month_heh["base"]) / calibrated["base"], 1)
            if calibrated["base"]
            else 0.0,
            "high": round(float(month_heh["high"]) / calibrated["low"], 1)
            if calibrated["low"]
            else 0.0,
        }
    direction = {
        key: round(operator_direction[key], 2) for key in ("low", "base", "high")
    }
    agentic_power = {
        "low": round(heh["low"] / direction["high"], 1),
        "base": round(heh["base"] / direction["base"], 1),
        "high": round(heh["high"] / direction["low"], 1),
    }
    repository_commits = int(
        (repository_aggregate or {}).get("default_branch_non_merge_commits", 0)
    )
    repository_count = int((repository_aggregate or {}).get("repository_count", 0))
    return {
        "schema_version": 1,
        "framework": {
            "name": "The Agentic Power Framework",
            "version": "1.4.0 draft",
            "author": "Dr. Mark Allen / HeroForge.AI",
            "url": "https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/",
            "formula": "AP = Skilled Human-Equivalent Hours / Human Direction Hours",
        },
        "updated_at_utc": updated,
        "basis": "operator-calibrated provisional estimate",
        "scope": "Merged upstream pull requests plus redacted default-branch work from accessible repositories since 2025",
        "window": {
            "start": min(starts) if starts else None,
            "end": max(ends) if ends else None,
        },
        "public_accepted_pull_requests": len(work_items),
        "repository_default_branch_non_merge_commits": repository_commits,
        "repository_count": repository_count,
        "skilled_human_equivalent_hours": heh,
        "uncalibrated_github_direction_proxy_hours": proxy_direction,
        "direction_calibration": {
            "allocation_factor_vs_base_proxy": {
                key: round(direction_calibration[key], 4)
                for key in ("low", "base", "high")
            },
            "operator_estimated_hours_per_week": OPERATOR_DIRECTION_HOURS_PER_WEEK,
            "evidence_window_weeks": evidence_weeks,
            "basis": "Operator estimate of approximately 1.5 to 2.0 active direction hours per week across the evidence window; 1.75 hours is the midpoint.",
            "status": "estimated from operator recall; not reconstructed from time logs",
        },
        "modeled_human_direction_hours": direction,
        "agentic_power_x": agentic_power,
        "engineer_weeks_at_40h": round(heh["base"] / 40, 1),
        "monthly": monthly,
        "methodology": {
            "acceptance": "Upstream work must be merged in a project that passes the GitHub Contributors API gate. Other repository work must be an authored non-merge commit on the default branch.",
            "heh": "Merged pull requests use outcome-category baselines adjusted by files, commits, and nonlinear change complexity. Default-branch work uses conservative conventional-commit outcome classes. Line count alone never establishes HEH.",
            "direction_proxy": "The conservative GitHub proxy assigns briefing/review/correction allowances to accepted work plus an explicit per-active-repository-month setup, coordination, and maintenance allowance.",
            "direction_calibration": "The published direction denominator is calibrated to the operator-provided estimate of approximately 1.5 to 2.0 active direction hours per week, with 1.75 hours as the midpoint. Monthly allocation follows the relative shape of the conservative GitHub proxy because autonomous batches do not require separate human direction for every commit.",
            "range": "Low AP = low HEH / high direction; high AP = high HEH / low direction.",
            "privacy": "Repository names, commit messages, URLs, code, employer, and client details from the redacted lane are not published.",
            "limitation": "The 1.5-to-2.0-hours-per-week calibration is operator-estimated, not reconstructed from time logs. Default-branch presence is a repository-level acceptance proxy. Only repositories currently accessible to the supplied credential can be counted. This is not a completed Full Evidence Audit.",
        },
        "work_items": work_items,
    }


def render_agentic_power_svg(profile: dict[str, object]) -> str:
    heh = profile["skilled_human_equivalent_hours"]
    direction = profile["modeled_human_direction_hours"]
    power = profile["agentic_power_x"]
    weeks = float(profile["engineer_weeks_at_40h"])
    public_items = int(profile["public_accepted_pull_requests"])
    repository_commits = int(profile["repository_default_branch_non_merge_commits"])
    repository_count = int(profile["repository_count"])
    monthly = profile["monthly"]
    calibration = profile["direction_calibration"]
    max_heh = max(
        (float(month["skilled_human_equivalent_hours"]["base"]) for month in monthly),
        default=1.0,
    )
    max_ap = max(
        (float(month["agentic_power_x"]["base"]) for month in monthly),
        default=1.0,
    )
    panels: list[str] = []
    for year, title_y, baseline in (("2025", 294, 418), ("2026", 482, 606)):
        year_months = [
            month for month in monthly if str(month["month"]).startswith(year)
        ]
        if not year_months:
            continue
        plot_left = 120.0
        plot_width = 1038.0
        column_width = plot_width / len(year_months)
        chart_points: list[str] = []
        line_points: list[str] = []
        for index, month in enumerate(year_months):
            center = plot_left + column_width * (index + 0.5)
            month_heh = float(month["skilled_human_equivalent_hours"]["base"])
            month_ap = float(month["agentic_power_x"]["base"])
            bar_height = 78 * month_heh / max_heh if max_heh else 0
            bar_y = baseline - bar_height
            point_y = baseline - (78 * month_ap / max_ap if max_ap else 0)
            line_points.append(f"{center:.1f},{point_y:.1f}")
            bar_width = min(42.0, column_width * 0.48)
            chart_points.append(
                f"""
      <rect class="ap-bar" style="animation-delay:{index * 0.08:.2f}s" x="{center - bar_width / 2:.1f}" y="{bar_y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="7" fill="#2DE2C5" fill-opacity="0.22" stroke="#2DE2C5" stroke-opacity="0.50"/>
      <circle class="ap-point" style="animation-delay:{index * 0.14:.2f}s" cx="{center:.1f}" cy="{point_y:.1f}" r="4.5" fill="#F2A93B" stroke="#07111E" stroke-width="3"/>
      <text x="{center:.1f}" y="{point_y - 10:.1f}" fill="#F8C66E" font-size="11" font-weight="760" text-anchor="middle">{month_ap:.1f}×</text>
      <text x="{center:.1f}" y="{baseline + 22:.1f}" fill="#91A2B8" font-size="11" letter-spacing="0.8" text-anchor="middle">{dt.datetime.strptime(str(month['month']), '%Y-%m').strftime('%b').upper()}</text>"""
            )
        panels.append(
            f"""
  <g font-family="Inter,Segoe UI,sans-serif">
    <text x="42" y="{title_y}" fill="#F8FAFC" font-size="17" font-weight="780">{year}</text>
    <text x="42" y="{title_y + 20}" fill="#65758B" font-size="11">MONTHLY</text>
    <line x1="120" y1="{baseline}" x2="1158" y2="{baseline}" stroke="#26364D"/>
    <polyline class="ap-line" pathLength="1" points="{' '.join(line_points)}" fill="none" stroke="#F2A93B" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.78"/>
    {''.join(chart_points)}
  </g>"""
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="690" viewBox="0 0 1200 690" role="img" aria-labelledby="title desc">
  <title id="title">Modeled Agentic Power profile for Rudy Celekli</title>
  <desc id="desc">An operator-calibrated provisional estimate since 2025 of {power['base']} times Agentic Power, including {public_items} merged upstream pull requests and {repository_commits} redacted default-branch commits, with a month-by-month timeline.</desc>
  <defs>
    <style>
      .ap-line {{ stroke-dasharray: 1; stroke-dashoffset: 1; animation: draw-line 1.8s ease-out forwards; }}
      .ap-bar {{ opacity: 1; animation: breathe-bar 4s ease-in-out infinite; }}
      .ap-point {{ animation: point-pulse 3s ease-in-out infinite; }}
      @keyframes draw-line {{ to {{ stroke-dashoffset: 0; }} }}
      @keyframes breathe-bar {{ 0%, 100% {{ opacity: .62; }} 50% {{ opacity: 1; }} }}
      @keyframes point-pulse {{ 0%, 100% {{ opacity: .68; }} 50% {{ opacity: 1; }} }}
      @media (prefers-reduced-motion: reduce) {{
        .ap-line, .ap-bar, .ap-point {{ animation: none; opacity: 1; stroke-dashoffset: 0; }}
      }}
    </style>
    <linearGradient id="ap-bg" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#07111E"/>
      <stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="ap-line" x1="0" y1="0" x2="1" y2="0">
      <stop stop-color="#2DE2C5"/>
      <stop offset="0.52" stop-color="#4D7CFE"/>
      <stop offset="1" stop-color="#9B7CFF"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="690" rx="28" fill="url(#ap-bg)"/>
  <rect x="1" y="1" width="1198" height="688" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="32" width="72" height="4" rx="2" fill="url(#ap-line)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Inter,Segoe UI,sans-serif" font-size="28" font-weight="780">LIVE AGENTIC POWER SNAPSHOT</text>
  <text x="1158" y="64" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" text-anchor="end">OPERATOR CALIBRATED · {html.escape(str(profile['updated_at_utc']))} UTC</text>

  <g transform="translate(42 112)" font-family="Inter,Segoe UI,sans-serif">
    <text x="0" y="54" fill="#2DE2C5" font-size="52" font-weight="800">{float(heh['base']):,.1f}</text>
    <text x="0" y="82" fill="#91A2B8" font-size="12" letter-spacing="1.4">SKILLED HEH</text>
    <text x="221" y="52" fill="#65758B" font-size="40">÷</text>
    <text x="286" y="54" fill="#9B7CFF" font-size="52" font-weight="800">{float(direction['base']):,.1f}</text>
    <text x="286" y="82" fill="#91A2B8" font-size="10.5" letter-spacing="0.9">OPERATOR-EST. DIRECTION HOURS</text>
    <text x="522" y="52" fill="#65758B" font-size="40">=</text>
    <text x="596" y="58" fill="#F2A93B" font-size="68" font-weight="850">{float(power['base']):.1f}×</text>
    <text x="600" y="86" fill="#F8C66E" font-size="12" letter-spacing="1.4">PROVISIONAL AP</text>
  </g>

  <g transform="translate(868 103)" font-family="Inter,Segoe UI,sans-serif">
    <rect width="290" height="130" rx="18" fill="#0B1728" stroke="#9B7CFF" stroke-opacity="0.42"/>
    <text x="22" y="31" fill="#C4B5FD" font-size="12" font-weight="750" letter-spacing="1.3">CALIBRATED RANGE</text>
    <text x="22" y="73" fill="#F8FAFC" font-size="27" font-weight="780">{float(power['low']):.1f}× to {float(power['high']):.1f}×</text>
    <text x="22" y="101" fill="#91A2B8" font-size="13">{float(calibration['operator_estimated_hours_per_week']['low']):.1f}–{float(calibration['operator_estimated_hours_per_week']['high']):.1f} direction h/week</text>
    <text x="22" y="119" fill="#65758B" font-size="12">Not a completed evidence audit</text>
  </g>

  <line x1="42" y1="256" x2="1158" y2="256" stroke="#26364D"/>
  <text x="42" y="276" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10" letter-spacing="1">MONTH OVER MONTH · JAN 2025 TO PRESENT</text>
  <g transform="translate(1158 276)" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10" text-anchor="end">
    <text x="-122" fill="#2DE2C5">BAR = ACCEPTED HEH</text>
    <text fill="#F2A93B">LINE = AP</text>
  </g>
  {''.join(panels)}
  <line x1="42" y1="648" x2="1158" y2="648" stroke="#26364D"/>
  <g transform="translate(42 674)" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11">
    <text fill="#2DE2C5">{public_items} merged upstream PRs</text>
    <text x="169" fill="#65758B">•</text>
    <text x="190" fill="#9B7CFF">{repository_commits:,} redacted default-branch commits</text>
    <text x="439" fill="#65758B">•</text>
    <text x="460" fill="#F8FAFC">{repository_count} accessible repos</text>
    <text x="580" fill="#65758B">•</text>
    <text x="601" fill="#F8FAFC">{weeks:.1f} engineer-weeks</text>
    <text x="1116" fill="#65758B" text-anchor="end">AP = HEH ÷ human direction</text>
  </g>
</svg>
"""


def render_repository_card(
    repo: dict[str, object], index: int, repository_count: int
) -> str:
    """Render one reusable repository evidence component."""
    row = index // 2
    is_single_last = repository_count % 2 == 1 and index == repository_count - 1
    x = 325 if is_single_last else 42 + (index % 2) * 567
    y = 112 + row * 258
    merged = int(repo["merged_prs"])
    commits = int(repo["contributor_commits"])
    added = int(repo["accepted_additions"])
    deleted = int(repo["accepted_deletions"])
    files = int(repo["accepted_changed_files"])
    accepted_lines = added + deleted
    description_lines = wrap_svg_text(repo["description"], width=65, lines=2)
    description = "".join(
        f'<tspan x="31" dy="{0 if line_index == 0 else 18}">{html.escape(line)}</tspan>'
        for line_index, line in enumerate(description_lines)
    )
    return f"""
    <g transform="translate({x} {y})">
      <rect width="550" height="240" rx="22" fill="#0B1728" stroke="{repo['accent']}" stroke-opacity="0.42"/>
      <rect class="signal" x="31" y="23" width="48" height="3" rx="1.5" fill="{repo['accent']}"/>
      <text x="31" y="55" fill="#F8FAFC" font-size="22" font-weight="760">{html.escape(ellipsize(repo['name'], 24))}</text>
      <text x="519" y="52" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11" text-anchor="end">{compact(int(repo['stargazers']))} STARS · {compact(int(repo['forks']))} FORKS</text>
      <text x="31" y="81" fill="#91A2B8" font-size="12.5">{description}</text>
      <g transform="translate(31 148)">
        {metric(0, 'repo commits', str(commits), str(repo['accent']))}
        {metric(122, 'merged PRs', str(merged), str(repo['secondary']))}
        {metric(238, 'files accepted', compact(files), '#F8FAFC')}
        {metric(366, 'line changes', compact(accepted_lines), '#F8FAFC')}
      </g>
      <line x1="31" y1="194" x2="519" y2="194" stroke="#203149"/>
      <text x="31" y="222" fill="#91A2B8" font-size="12">accepted code</text>
      <text x="128" y="222" fill="{repo['accent']}" font-size="14" font-weight="700">+{added:,}</text>
      <text x="216" y="222" fill="#F87171" font-size="14" font-weight="700">−{deleted:,}</text>
      <text x="519" y="222" fill="#65758B" font-size="11.5" text-anchor="end">GitHub-listed contributor</text>
    </g>"""


def render_contribution_footer(
    repositories: list[dict[str, object]], footer_y: int
) -> str:
    """Render aggregate proof and freshness as one reusable summary component."""
    total_merged = sum(int(repo["merged_prs"]) for repo in repositories)
    total_commits = sum(int(repo["contributor_commits"]) for repo in repositories)
    total_stars = sum(int(repo["stargazers"]) for repo in repositories)
    return f"""
  <g transform="translate(42 {footer_y})" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12">
    <text fill="#2DE2C5">{len(repositories)} verified projects</text>
    <text x="152" fill="#65758B">•</text>
    <text x="172" fill="#9B7CFF">{total_merged} merged PRs</text>
    <text x="292" fill="#65758B">•</text>
    <text x="312" fill="#F8FAFC">{compact(total_commits)} repo commits</text>
    <text x="445" fill="#65758B">•</text>
    <text x="465" fill="#F2A93B">{compact(total_stars)} combined stars</text>
    <text x="1116" fill="#65758B" text-anchor="end">official contributor evidence · refreshed hourly</text>
  </g>"""


def render_svg(repositories: list[dict[str, object]], updated: str) -> str:
    cards = [
        render_repository_card(repo, index, len(repositories))
        for index, repo in enumerate(repositories)
    ]
    rows = max(math.ceil(len(repositories) / 2), 1)
    height = 112 + rows * 240 + max(rows - 1, 0) * 18 + 72
    footer_y = height - 34
    footer = render_contribution_footer(repositories, footer_y)
    project_names = ", ".join(str(repo["name"]) for repo in repositories)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
  <title id="title">Open-source contribution evidence for Rudy Celekli</title>
  <desc id="desc">GitHub-verified contribution totals for {html.escape(project_names)}. Repository stars and forks describe project reach, not personal contribution.</desc>
  <defs>
    <style>
      .signal {{ animation: signal-pulse 3.8s ease-in-out infinite; }}
      @keyframes signal-pulse {{ 0%, 100% {{ opacity: .58; }} 50% {{ opacity: 1; }} }}
      @media (prefers-reduced-motion: reduce) {{ .signal {{ animation: none; opacity: 1; }} }}
    </style>
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
  <rect width="1200" height="{height}" rx="28" fill="url(#canvas)"/>
  <rect x="1" y="1" width="1198" height="{height - 2}" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="32" width="66" height="4" rx="2" fill="url(#rule)"/>
  <text x="42" y="70" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="29" font-weight="780">OFFICIAL OPEN-SOURCE CONTRIBUTOR</text>
  <text x="1158" y="68" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" text-anchor="end">GITHUB · {html.escape(updated)} UTC</text>
  <g font-family="Avenir Next,Segoe UI,sans-serif">
    {''.join(cards)}
  </g>
  {footer}
</svg>
"""


def render_readme_section(repositories: list[dict[str, object]], updated: str) -> str:
    lines = [
        "<!-- contribution-stats:start -->",
        "## Official open-source contributor",
        "",
        "This section discovers my merged upstream work automatically and includes **only projects where GitHub lists me as a contributor**. Accepted-code totals count merged pull requests only; stars and forks describe repository reach, not personal credit.",
        "",
        f'<img src="./assets/open-source-contributions.svg" width="100%" alt="GitHub-verified contribution statistics for {html.escape(", ".join(str(repo["name"]) for repo in repositories))}" />',
        "",
    ]
    for repo in repositories:
        lines.append(
            f"- **[{repo['name']}]({repo['url']})**: "
            f"[GitHub-listed contributor]({repo['contributors_url']}) with "
            f"{int(repo['contributor_commits']):,} repository commits and "
            f"[{int(repo['merged_prs']):,} merged PRs]({repo['pull_requests_url']}); "
            f"+{int(repo['accepted_additions']):,} / −{int(repo['accepted_deletions']):,} "
            f"accepted lines across {int(repo['accepted_changed_files']):,} changed files. "
            f"Repository reach: {int(repo['stargazers']):,} stars and {int(repo['forks']):,} forks."
        )
    lines.extend(
        [
            "",
            f"<sub>Last verified {updated} UTC · visual + evidence refreshed hourly by [GitHub Actions](./.github/workflows/refresh-contribution-stats.yml) · [machine-readable evidence](./data/contributions.json)</sub>",
            "<!-- contribution-stats:end -->",
        ]
    )
    return "\n".join(lines)


def render_agentic_power_readme(profile: dict[str, object]) -> str:
    power = profile["agentic_power_x"]
    heh = profile["skilled_human_equivalent_hours"]
    direction = profile["modeled_human_direction_hours"]
    proxy_direction = profile["uncalibrated_github_direction_proxy_hours"]
    calibration = profile["direction_calibration"]
    weeks = float(profile["engineer_weeks_at_40h"])
    public_items = int(profile["public_accepted_pull_requests"])
    repository_commits = int(profile["repository_default_branch_non_merge_commits"])
    repository_count = int(profile["repository_count"])
    return "\n".join(
        [
            "<!-- agentic-power-profile:start -->",
            "### Live Agentic Power snapshot",
            "",
            '<img src="./assets/agentic-power-profile.svg" width="100%" alt="Operator-calibrated provisional Agentic Power since 2025 with a month-by-month timeline" />',
            "",
            f"**AP ≈ {float(power['base']):.1f}× (operator-calibrated provisional estimate):** approximately "
            f"{float(heh['base']):,.1f} skilled Human-Equivalent Hours, or {weeks:.1f} engineer-weeks, "
            f"divided by {float(direction['base']):,.1f} operator-estimated human-direction hours. "
            f"The transparent scenario range is {float(power['low']):.1f}× to {float(power['high']):.1f}×.",
            "",
            f"The evidence base since January 2025 combines **{public_items:,} merged upstream PRs** with **{repository_commits:,} authored, non-merge default-branch commits across {repository_count} currently accessible repositories**: personal, private, employer, and open-source alike. Work such as Gradia is included only as a redacted aggregate: no repository names, commit messages, code, links, employer, or client details are published.",
            "",
            f"The conservative GitHub activity proxy produces {float(proxy_direction['base']):,.1f} direction hours because it assigns attention to individual commits and repository-months. Operator recall is **{float(calibration['operator_estimated_hours_per_week']['low']):.1f}–{float(calibration['operator_estimated_hours_per_week']['high']):.1f} active direction hours per week**; across {float(calibration['evidence_window_weeks']):.1f} weeks, that calibrates the denominator to {float(direction['low']):,.1f}–{float(direction['high']):,.1f} hours, with {float(direction['base']):,.1f} as the midpoint.",
            "",
            "Direction includes active briefing, steering, reviewing, correcting, and coordinating. It excludes agent runtime and waiting. The calibration is operator-estimated rather than reconstructed from time logs, so this remains **a transparent scenario, not a completed Full Evidence Audit**.",
            "",
            "<sub>[Framework and formula](https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/) · [calculation evidence](./data/agentic-power.json) · public upstream evidence and visuals refreshed hourly; redacted repository snapshot retained until a private read credential is available</sub>",
            "<!-- agentic-power-profile:end -->",
        ]
    )


def update_readme(marker: str, section: str) -> None:
    current = README.read_text()
    pattern = re.compile(
        rf"<!-- {re.escape(marker)}:start -->.*?<!-- {re.escape(marker)}:end -->",
        re.DOTALL,
    )
    updated, replacements = pattern.subn(section, current)
    if replacements != 1:
        raise RuntimeError(f"Expected one {marker} section, found {replacements}")
    README.write_text(updated)


def write_generated(path: Path, content: str) -> None:
    """Write deterministic generated text with no trailing whitespace."""
    path.write_text("\n".join(line.rstrip() for line in content.splitlines()) + "\n")


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN is required", file=sys.stderr)
        return 2

    updated = dt.datetime.now(dt.timezone.utc).date().isoformat()
    repositories = discover_repositories(token)
    if not repositories:
        raise RuntimeError(
            "No public upstream repositories passed the merged-PR and official-contributor gates"
        )
    private_token = os.environ.get("PRIVATE_GITHUB_TOKEN")
    raw_exclusions = os.environ.get("AP_EXCLUDED_REPOSITORIES", "")
    excluded = {
        value.strip().casefold()
        for value in raw_exclusions.split(",")
        if value.strip()
    }
    excluded.update(str(repo["full_name"]).casefold() for repo in repositories)
    if private_token:
        if not raw_exclusions.strip():
            raise RuntimeError(
                "AP_EXCLUDED_REPOSITORIES is required when refreshing the "
                "access-controlled repository aggregate"
            )
        repository_aggregate = collect_repository_aggregate(
            private_token, updated, excluded
        )
        REPOSITORY_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPOSITORY_DATA_FILE.write_text(
            json.dumps(repository_aggregate, indent=2, sort_keys=True) + "\n"
        )
    elif REPOSITORY_DATA_FILE.exists():
        repository_aggregate = json.loads(REPOSITORY_DATA_FILE.read_text())
    else:
        repository_aggregate = None
    agentic_power = calculate_agentic_power(
        repositories, updated, repository_aggregate
    )
    public_repositories = [
        {key: value for key, value in repo.items() if not key.startswith("_")}
        for repo in repositories
    ]
    payload = {
        "schema_version": 2,
        "source": "GitHub GraphQL and REST APIs",
        "login": LOGIN,
        "updated_at_utc": updated,
        "methodology": {
            "project_discovery": "Every public, non-fork repository outside the login's own account with a merged pull request authored by the login is discovered automatically on each run.",
            "project_inclusion": "A discovered repository is included only when GitHub's Contributors API also lists the login.",
            "contributor_commits": "Commit count reported by GitHub's Contributors API.",
            "accepted_code": "Additions, deletions, and changed files from merged pull requests only.",
            "repository_reach": "Stars and forks are current repository-level context, not personal contribution credit.",
        },
        "repositories": public_repositories,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_generated(DATA_FILE, json.dumps(payload, indent=2, sort_keys=True))
    write_generated(
        AP_DATA_FILE, json.dumps(agentic_power, indent=2, sort_keys=True)
    )
    write_generated(SVG_FILE, render_svg(repositories, updated))
    write_generated(AP_SVG_FILE, render_agentic_power_svg(agentic_power))
    update_readme(
        "agentic-power-profile", render_agentic_power_readme(agentic_power)
    )
    update_readme(
        "contribution-stats", render_readme_section(repositories, updated)
    )
    print(
        "Updated official contributor evidence: "
        + ", ".join(
            f"{repo['name']}={repo['contributor_commits']} commits/{repo['merged_prs']} merged PRs"
            for repo in repositories
        )
    )
    if repository_aggregate:
        print(
            "Updated redacted repository evidence since 2025: "
            f"{repository_aggregate['default_branch_non_merge_commits']} commits across "
            f"{repository_aggregate['repository_count']} repositories"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
