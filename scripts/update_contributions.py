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
HERO_SVG_FILE = ROOT / "assets" / "hero.svg"
BUILDING_SVG_FILE = ROOT / "assets" / "building-now.svg"
AUTHORITY_SVG_FILE = ROOT / "assets" / "contributor-authority.svg"
LATEST_WORK_SVG_FILE = ROOT / "assets" / "latest-verified-work.svg"
SVG_FILE = ROOT / "assets" / "open-source-contributions.svg"
IMPACT_SVG_FILE = ROOT / "assets" / "impact-signature.svg"
OWNED_SVG_FILE = ROOT / "assets" / "owned-public-projects.svg"
AP_DATA_FILE = ROOT / "data" / "agentic-power.json"
AP_SVG_FILE = ROOT / "assets" / "agentic-power-profile.svg"
WALKTHROUGH_SVG_FILE = ROOT / "assets" / "profile-walkthrough.svg"
REPOSITORY_DATA_FILE = ROOT / "data" / "repository-work-aggregate.json"
LOGIN = "rudycelekli"
OWNED_PROJECT_DISPLAY_LIMIT = 8
FEATURED_OWNED_PROJECT_LIMIT = 6
BUILDING_FRONTIER_PAGE_SIZE = 3
README_EVIDENCE_PREVIEW_LIMIT = 10
KYA_EVIDENCE_FULL_NAME = f"{LOGIN}/kya-evidence"
# The profile repository is presentation infrastructure, not a product. The
# user has also explicitly identified DreamMachine as not their authored work.
OWNED_PROJECT_EXCLUSIONS = {
    f"{LOGIN}/{LOGIN}".casefold(),
    f"{LOGIN}/DreamMachine".casefold(),
}
PERIOD_START = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
CURRENT_OPEN_PULL_REQUESTS_URL = (
    "https://github.com/search?q=author%3Arudycelekli+is%3Apr+is%3Aopen+"
    "created%3A%3E%3D2025-01-01&type=pullrequests"
)
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
OWNED_PRESENTATION_OVERRIDES = {
    f"{LOGIN}/testlore": {"name": "TestLore"},
    f"{LOGIN}/proofseal": {"name": "ProofSeal"},
    f"{LOGIN}/gradia-guard": {"name": "Gradia Guard"},
    f"{LOGIN}/kin-connect": {"name": "Kin Connect"},
    KYA_EVIDENCE_FULL_NAME: {"name": "KYA Evidence"},
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
        state
        isDraft
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
          visibility
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

COMMIT_CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      commitContributionsByRepository(maxRepositories: 100) {
        repository {
          name
          nameWithOwner
          description
          url
          isPrivate
          visibility
          isFork
          stargazerCount
          forkCount
          owner { login }
        }
        contributions(first: 100) {
          totalCount
          pageInfo { hasNextPage }
          nodes {
            commitCount
            occurredAt
            url
          }
        }
      }
    }
  }
}
"""


def graphql_data(
    token: str, query: str, variables: dict[str, object]
) -> dict[str, object]:
    """Run a GitHub GraphQL query with retry and rate-limit handling."""
    body = json.dumps({"query": query, "variables": variables}).encode()
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
        return payload["data"]
    raise RuntimeError("GitHub GraphQL failed after retries")


def graphql(token: str, variables: dict[str, object]) -> dict[str, object]:
    """Compatibility wrapper for exhaustive authored-PR search."""
    return graphql_data(token, DISCOVERY_QUERY, variables)["search"]


def cached_contribution_stats(full_name: str) -> tuple[int | None, int | None]:
    """Return the last published contributor evidence when GitHub is pending."""
    if not DATA_FILE.exists():
        return None, None
    try:
        payload = json.loads(DATA_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    candidates = list(payload.get("repositories") or []) + list(
        payload.get("owned_repositories") or []
    )
    for repo in candidates:
        if str(repo.get("full_name", "")).casefold() != full_name.casefold():
            continue
        if not bool(repo.get("official_contributor")) and str(
            repo.get("verification_tier") or ""
        ) != "github_listed_contributor":
            return None, None
        count = repo.get("contributor_commits")
        rank = repo.get("contributor_rank")
        return (
            int(count) if count is not None else None,
            int(rank) if rank is not None else None,
        )
    return None, None


def official_contribution_stats(
    token: str, full_name: str
) -> tuple[int | None, int | None]:
    """Return GitHub's indexed commit count and all-time contributor rank."""
    page = 1
    while True:
        try:
            contributors = rest_json(
                token,
                f"https://api.github.com/repos/{full_name}/contributors"
                f"?per_page=100&page={page}",
            )
        except RuntimeError as error:
            # GitHub may return 202 with an empty body while computing a
            # repository's contributor statistics. Missing this optional tier
            # must not abort the complete portfolio refresh.
            cached = cached_contribution_stats(full_name)
            print(
                f"warning: contributor index unavailable for {full_name}; "
                f"using cached evidence {cached}: {error}",
                file=sys.stderr,
            )
            return cached
        if not isinstance(contributors, list):
            cached = cached_contribution_stats(full_name)
            print(
                f"warning: unexpected contributor index for {full_name}; "
                f"using cached evidence {cached}",
                file=sys.stderr,
            )
            return cached

        for index, contributor in enumerate(contributors):
            if contributor.get("login", "").casefold() == LOGIN.casefold():
                rank = (page - 1) * 100 + index + 1
                return int(contributor["contributions"]), rank
        if len(contributors) < 100:
            return None, None
        page += 1


def official_contribution_count(token: str, full_name: str) -> int | None:
    """Compatibility wrapper for call sites that only need the commit count."""
    count, _rank = official_contribution_stats(token, full_name)
    return count


def cached_latest_release(full_name: str) -> dict[str, object] | None:
    """Return the last published release signal when GitHub is unavailable."""
    if not DATA_FILE.exists():
        return None
    try:
        payload = json.loads(DATA_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for repo in payload.get("owned_repositories") or []:
        if str(repo.get("full_name", "")).casefold() == full_name.casefold():
            release = repo.get("latest_release")
            return release if isinstance(release, dict) else None
    return None


def discover_latest_release(
    token: str, full_name: str
) -> dict[str, object] | None:
    """Return the newest public, non-draft GitHub release or prerelease."""
    try:
        releases = rest_json(
            token,
            f"https://api.github.com/repos/{full_name}/releases?per_page=5",
        )
    except RuntimeError as error:
        cached = cached_latest_release(full_name)
        print(
            f"warning: release index unavailable for {full_name}; "
            f"using cached release {cached}: {error}",
            file=sys.stderr,
        )
        return cached
    if not isinstance(releases, list):
        return cached_latest_release(full_name)
    for release in releases:
        if bool(release.get("draft")):
            continue
        return {
            "tag_name": str(release.get("tag_name") or "release"),
            "name": str(
                release.get("name") or release.get("tag_name") or "Release"
            ),
            "url": str(release.get("html_url") or ""),
            "published_at": release.get("published_at"),
            "prerelease": bool(release.get("prerelease")),
        }
    return None


def default_branch_contribution_count(token: str, full_name: str) -> int:
    """Count authored commits that GitHub exposes on the default branch."""
    total = 0
    page = 1
    while True:
        commits = rest_json(
            token,
            f"https://api.github.com/repos/{full_name}/commits"
            f"?author={LOGIN}&per_page=100&page={page}",
        )
        if not isinstance(commits, list):
            raise RuntimeError(
                f"Unexpected GitHub commit response for {full_name}"
            )
        total += len(commits)
        if len(commits) < 100:
            return total
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
        except json.JSONDecodeError as error:
            # GitHub occasionally closes a successful REST response before a
            # JSON body reaches the runner. Treat that as transient instead of
            # letting one repository abort the complete scheduled refresh.
            if attempt < 2:
                time.sleep(min(2**attempt, 16))
                continue
            raise RuntimeError(
                f"GitHub REST API returned invalid JSON for {url}"
            ) from error
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


def discover_current_open_pull_request_count(token: str) -> int:
    """Count every public PR authored in the evidence window that is open now."""
    query = (
        f"author:{LOGIN} is:pr is:open is:public "
        f"created:>={PERIOD_START.date().isoformat()}"
    )
    parameters = urllib.parse.urlencode({"q": query, "per_page": 1})
    payload = rest_json(token, f"https://api.github.com/search/issues?{parameters}")
    if not isinstance(payload, dict) or "total_count" not in payload:
        raise RuntimeError("Unexpected GitHub open pull-request search response")
    if payload.get("incomplete_results"):
        raise RuntimeError("GitHub open pull-request search returned incomplete results")
    return int(payload["total_count"])


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


IMPACT_CATEGORY_RULES = (
    (
        "Security & trust",
        ("security", "auth", "permission", "credential", "injection", "secret", "vulnerability"),
    ),
    (
        "Testing & evaluation",
        ("test", "benchmark", "eval", "coverage", "assert", "fixture", "quality"),
    ),
    (
        "Performance & scale",
        ("performance", "perf:", "latency", "speed", "cache", "parallel", "concurr", "memory leak", "optimiz"),
    ),
    (
        "Reliability & correctness",
        ("fix", "bug", "error", "validate", "validation", "retry", "timeout", "race", "serialize", "preserve", "reject", "fallback", "crash"),
    ),
    (
        "Agent infrastructure",
        ("agent", "mcp", "tool", "memory", "orchestrat", "protocol", "skill", "worker"),
    ),
    (
        "Developer experience",
        ("docs", "readme", "cli", "install", "config", "typing", "types", "developer", "example"),
    ),
    (
        "Product capability",
        ("feat", "add", "support", "implement", "enable", "introduce", "create"),
    ),
)


def classify_pull_request_impact(title: object) -> str:
    """Classify a merged PR title with deterministic, inspectable keyword rules."""
    lowered = str(title or "").casefold()
    for category, keywords in IMPACT_CATEGORY_RULES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return "Maintenance & integration"


def summarize_repository_impact(
    merged_pull_requests: list[dict[str, object]], verification_tier: str
) -> dict[str, object]:
    """Summarize contribution impact while retaining the complete category mix."""
    if not merged_pull_requests:
        category = (
            "Default-branch engineering"
            if verification_tier == "default_branch_commit_verified"
            else "Repository contribution"
        )
        return {
            "primary": category,
            "primary_prs": 0,
            "classified_prs": 0,
            "breakdown": {},
            "basis": "GitHub commit attribution",
        }

    counts: dict[str, int] = {}
    for pull_request in merged_pull_requests:
        category = classify_pull_request_impact(pull_request.get("title"))
        counts[category] = counts.get(category, 0) + 1
    category_order = {
        category: index for index, (category, _keywords) in enumerate(IMPACT_CATEGORY_RULES)
    }
    category_order["Maintenance & integration"] = len(category_order)
    ordered = sorted(
        counts.items(),
        key=lambda item: (-item[1], category_order.get(item[0], 999), item[0]),
    )
    primary, primary_count = ordered[0]
    return {
        "primary": primary,
        "primary_prs": primary_count,
        "classified_prs": len(merged_pull_requests),
        "breakdown": dict(ordered),
        "basis": "Merged PR title classification",
    }


def build_impact_analysis(
    repositories: list[dict[str, object]],
) -> dict[str, object]:
    """Aggregate deterministic PR-title impact signals across repositories."""
    counts: dict[str, int] = {}
    projects: dict[str, set[str]] = {}
    primary_projects: dict[str, int] = {}
    for repository in repositories:
        full_name = str(repository.get("full_name") or repository.get("name") or "")
        primary = str(repository.get("impact_category") or "")
        if primary:
            primary_projects[primary] = primary_projects.get(primary, 0) + 1
        for category, value in dict(repository.get("impact_breakdown") or {}).items():
            count = int(value)
            if not count:
                continue
            counts[str(category)] = counts.get(str(category), 0) + count
            projects.setdefault(str(category), set()).add(full_name)

    total = sum(counts.values())
    categories = []
    for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        categories.append(
            {
                "category": category,
                "merged_prs": count,
                "share_pct": round((count / total * 100) if total else 0, 1),
                "projects_touched": len(projects.get(category, set())),
                "primary_for_projects": primary_projects.get(category, 0),
            }
        )

    dominant = categories[0] if categories else {
        "category": "No classified impact",
        "merged_prs": 0,
        "share_pct": 0.0,
        "projects_touched": 0,
    }
    secondary_breadth = max(
        (item for item in categories if item["category"] != dominant["category"]),
        key=lambda item: (int(item["projects_touched"]), int(item["merged_prs"])),
        default=dominant,
    )
    quality_categories = {
        "Reliability & correctness",
        "Testing & evaluation",
        "Security & trust",
    }
    quality_count = sum(
        int(item["merged_prs"])
        for item in categories
        if item["category"] in quality_categories
    )
    quality_share = round((quality_count / total * 100) if total else 0, 1)
    patterns = [
        {
            "label": "DOMINANT CHANGE INTENT",
            "value": f"{dominant['share_pct']:.1f}% {str(dominant['category']).lower()}",
            "detail": f"{int(dominant['merged_prs']):,} merged PRs across {int(dominant['projects_touched'])} projects",
        },
        {
            "label": "QUALITY + TRUST SIGNAL",
            "value": f"{quality_share:.1f}% of classified merged work",
            "detail": "Reliability, testing and security signals combined",
        },
        {
            "label": "SECONDARY BREADTH",
            "value": f"{secondary_breadth['category']} spans {int(secondary_breadth['projects_touched'])} projects",
            "detail": f"{int(secondary_breadth['merged_prs']):,} merged PRs carry this signal",
        },
    ]
    return {
        "classified_merged_prs": total,
        "active_categories": len(categories),
        "categories": categories,
        "patterns": patterns,
        "methodology": "Exclusive dominant-intent classification from merged PR titles. Categories sum to merged PRs; they describe title signals, not a full semantic code audit.",
    }


def markdown_table_description(value: object, limit: int = 150) -> str:
    """Return one compact, table-safe repository purpose sentence."""
    description = html.escape(ellipsize(readable_description(value), limit)).replace(
        "|", "\\|"
    )
    if description and description[-1] not in ".!?…":
        description += "."
    return description


def search_pull_requests(
    token: str,
    start: dt.date,
    end: dt.date,
    *,
    merged_only: bool,
) -> list[dict[str, object]]:
    """Exhaust GitHub PR search, splitting date windows above its result cap."""
    status = " is:merged" if merged_only else ""
    date_field = "merged" if merged_only else "created"
    query = (
        f"is:pr author:{LOGIN}{status} "
        f"{date_field}:{start.isoformat()}..{end.isoformat()}"
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
                    f"More than 1,000 matching PRs occurred on {start}; "
                    "GitHub search cannot exhaust this day"
                )
            midpoint = start + (end - start) // 2
            return search_pull_requests(
                token, start, midpoint, merged_only=merged_only
            ) + search_pull_requests(
                token,
                midpoint + dt.timedelta(days=1),
                end,
                merged_only=merged_only,
            )
        pull_requests.extend(node for node in result["nodes"] if node)
        page = result["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    if len(pull_requests) != expected_total:
        raise RuntimeError(
            f"Expected {expected_total} matching PRs for {LOGIN}, "
            f"received {len(pull_requests)}"
        )
    return pull_requests


def search_merged_pull_requests(
    token: str, start: dt.date, end: dt.date
) -> list[dict[str, object]]:
    """Return every merged pull request authored by the profile login."""
    return search_pull_requests(token, start, end, merged_only=True)


def search_authored_pull_requests(
    token: str, start: dt.date, end: dt.date
) -> list[dict[str, object]]:
    """Return every authored PR so default-branch acceptance can also be found."""
    return search_pull_requests(token, start, end, merged_only=False)


def upstream_project_exclusions() -> set[str]:
    """Return private policy exclusions without publishing their values."""
    raw = os.environ.get("PROFILE_UPSTREAM_EXCLUSIONS", "")
    required = os.environ.get("REQUIRE_PROFILE_UPSTREAM_EXCLUSIONS", "").lower()
    if required in {"1", "true", "yes"} and not raw.strip():
        raise RuntimeError(
            "PROFILE_UPSTREAM_EXCLUSIONS is required for this public refresh"
        )
    return {
        value.strip().casefold()
        for value in re.split(r"[,\n]", raw)
        if value.strip()
    }


def discover_commit_contribution_repositories(
    token: str,
    start: dt.date = dt.date(2008, 1, 1),
    end: dt.date | None = None,
) -> dict[str, dict[str, object]]:
    """Discover every public upstream repository credited by GitHub's graph.

    GitHub groups commit contributions by day and caps nested connections at
    100 nodes. Windows are split recursively whenever connection pagination
    says that repository discovery or daily contribution groups were truncated.
    """
    end = end or dt.datetime.now(dt.timezone.utc).date()
    if end < start:
        return {}

    def collect_window(
        window_start: dt.date, window_end: dt.date
    ) -> dict[str, dict[str, object]]:
        data = graphql_data(
            token,
            COMMIT_CONTRIBUTIONS_QUERY,
            {
                "login": LOGIN,
                "from": f"{window_start.isoformat()}T00:00:00Z",
                "to": f"{window_end.isoformat()}T23:59:59Z",
            },
        )
        user = data.get("user") or {}
        collection = user.get("contributionsCollection") or {}
        entries = list(collection.get("commitContributionsByRepository") or [])
        could_be_truncated = len(entries) >= 100 or any(
            bool(
                ((entry.get("contributions") or {}).get("pageInfo") or {}).get(
                    "hasNextPage"
                )
            )
            for entry in entries
        )
        if could_be_truncated:
            if window_start == window_end:
                raise RuntimeError(
                    "GitHub contribution graph pagination exceeded its 100-day "
                    "group cap on "
                    f"{window_start.isoformat()}"
                )
            midpoint = window_start + (window_end - window_start) // 2
            left = collect_window(window_start, midpoint)
            right = collect_window(midpoint + dt.timedelta(days=1), window_end)
            for full_name, candidate in right.items():
                existing = left.setdefault(
                    full_name,
                    {
                        "repository": candidate["repository"],
                        "contribution_graph_commits": 0,
                        "contribution_graph_evidence_url": candidate.get(
                            "contribution_graph_evidence_url"
                        ),
                        "contribution_graph_latest_at": candidate.get(
                            "contribution_graph_latest_at"
                        ),
                    },
                )
                existing["repository"] = candidate["repository"]
                existing["contribution_graph_commits"] = int(
                    existing["contribution_graph_commits"]
                ) + int(candidate["contribution_graph_commits"])
                if str(candidate.get("contribution_graph_latest_at") or "") > str(
                    existing.get("contribution_graph_latest_at") or ""
                ):
                    existing["contribution_graph_latest_at"] = candidate.get(
                        "contribution_graph_latest_at"
                    )
                    existing["contribution_graph_evidence_url"] = candidate.get(
                        "contribution_graph_evidence_url"
                    )
            return left

        discovered: dict[str, dict[str, object]] = {}
        for entry in entries:
            repository = entry.get("repository") or {}
            full_name = str(repository.get("nameWithOwner") or "")
            contributions = entry.get("contributions") or {}
            nodes = list(contributions.get("nodes") or [])
            commit_count = sum(int(node.get("commitCount") or 0) for node in nodes)
            if not full_name or not commit_count:
                continue
            latest = max(
                nodes,
                key=lambda node: str(node.get("occurredAt") or ""),
                default={},
            )
            discovered[full_name] = {
                "repository": repository,
                "contribution_graph_commits": commit_count,
                "contribution_graph_evidence_url": str(latest.get("url") or ""),
                "contribution_graph_latest_at": latest.get("occurredAt"),
            }
        return discovered

    discovered: dict[str, dict[str, object]] = {}
    cursor = start
    while cursor <= end:
        window_end = min(
            dt.date(cursor.year, 12, 31),
            end,
        )
        for full_name, candidate in collect_window(cursor, window_end).items():
            existing = discovered.setdefault(
                full_name,
                {
                    "repository": candidate["repository"],
                    "contribution_graph_commits": 0,
                    "contribution_graph_evidence_url": candidate.get(
                        "contribution_graph_evidence_url"
                    ),
                    "contribution_graph_latest_at": candidate.get(
                        "contribution_graph_latest_at"
                    ),
                },
            )
            existing["repository"] = candidate["repository"]
            existing["contribution_graph_commits"] = int(
                existing["contribution_graph_commits"]
            ) + int(candidate["contribution_graph_commits"])
            if str(candidate.get("contribution_graph_latest_at") or "") > str(
                existing.get("contribution_graph_latest_at") or ""
            ):
                existing["contribution_graph_latest_at"] = candidate.get(
                    "contribution_graph_latest_at"
                )
                existing["contribution_graph_evidence_url"] = candidate.get(
                    "contribution_graph_evidence_url"
                )
        cursor = window_end + dt.timedelta(days=1)
    return discovered


def discover_repositories(token: str) -> list[dict[str, object]]:
    """Discover public upstream repos from every GitHub attribution surface."""
    excluded = upstream_project_exclusions()
    pull_requests = search_authored_pull_requests(
        token, dt.date(2008, 1, 1), dt.datetime.now(dt.timezone.utc).date()
    )
    graph_repositories = discover_commit_contribution_repositories(token)

    grouped: dict[str, dict[str, object]] = {}
    for pull_request in pull_requests:
        repository = pull_request.get("repository") or {}
        full_name = str(repository.get("nameWithOwner", ""))
        owner = repository.get("owner") or {}
        if (
            not full_name
            or full_name.casefold() in excluded
            or bool(repository.get("isPrivate"))
            or str(repository.get("visibility") or "").upper() != "PUBLIC"
            or bool(repository.get("isFork"))
            or str(owner.get("login", "")).casefold() == LOGIN.casefold()
        ):
            continue
        entry = grouped.setdefault(
            full_name,
            {
                "repository": repository,
                "pull_requests": [],
                "contribution_graph_commits": 0,
                "contribution_graph_evidence_url": "",
                "contribution_graph_latest_at": None,
            },
        )
        entry["pull_requests"].append(pull_request)

    for full_name, graph_entry in graph_repositories.items():
        repository = graph_entry["repository"]
        owner = repository.get("owner") or {}
        if (
            not full_name
            or full_name.casefold() in excluded
            or bool(repository.get("isPrivate"))
            or str(repository.get("visibility") or "").upper() != "PUBLIC"
            or bool(repository.get("isFork"))
            or str(owner.get("login", "")).casefold() == LOGIN.casefold()
        ):
            continue
        entry = grouped.setdefault(
            full_name,
            {
                "repository": repository,
                "pull_requests": [],
                "contribution_graph_commits": 0,
                "contribution_graph_evidence_url": "",
                "contribution_graph_latest_at": None,
            },
        )
        entry["repository"] = repository
        entry["contribution_graph_commits"] = int(
            graph_entry["contribution_graph_commits"]
        )
        entry["contribution_graph_evidence_url"] = str(
            graph_entry.get("contribution_graph_evidence_url") or ""
        )
        entry["contribution_graph_latest_at"] = graph_entry.get(
            "contribution_graph_latest_at"
        )

    repositories: list[dict[str, object]] = []
    for full_name, entry in grouped.items():
        repository = entry["repository"]
        merged = [
            pull_request
            for pull_request in entry["pull_requests"]
            if bool(pull_request.get("merged"))
        ]
        open_pull_requests = [
            pull_request
            for pull_request in entry["pull_requests"]
            if str(pull_request.get("state") or "").upper() == "OPEN"
        ]
        graph_commits = int(entry.get("contribution_graph_commits") or 0)
        listed_commits = None
        contributor_rank = None
        default_branch_commits = 0
        if merged or graph_commits:
            listed_commits, contributor_rank = official_contribution_stats(
                token, full_name
            )
        if listed_commits is None:
            default_branch_commits = default_branch_contribution_count(
                token, full_name
            )
            if default_branch_commits and not merged:
                listed_commits, contributor_rank = official_contribution_stats(
                    token, full_name
                )
        if listed_commits is not None:
            verification_tier = "github_listed_contributor"
            verification_label = "GitHub-listed contributor"
            contributor_commits = listed_commits
        elif default_branch_commits:
            verification_tier = "default_branch_commit_verified"
            verification_label = "Default-branch commit verified"
            contributor_commits = default_branch_commits
        elif graph_commits:
            verification_tier = "github_contribution_graph_verified"
            verification_label = "GitHub graph verified"
            contributor_commits = graph_commits
        elif merged:
            verification_tier = "merged_pr_verified"
            verification_label = "Merged-PR verified"
            contributor_commits = 0
        else:
            continue
        impact = summarize_repository_impact(merged, verification_tier)
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
                "commits_url": (
                    f"https://github.com/{full_name}/commits"
                    f"?author={LOGIN}"
                ),
                "contribution_graph_evidence_url": str(
                    entry.get("contribution_graph_evidence_url") or ""
                ),
                "contribution_graph_latest_at": entry.get(
                    "contribution_graph_latest_at"
                ),
                "contribution_graph_commits": graph_commits,
                "pull_requests_url": (
                    f"https://github.com/{full_name}/pulls"
                    f"?q=is%3Apr+author%3A{LOGIN}+is%3Amerged"
                ),
                "open_pull_requests_url": (
                    f"https://github.com/{full_name}/pulls"
                    f"?q=is%3Apr+author%3A{LOGIN}+is%3Aopen"
                ),
                "verification_tier": verification_tier,
                "verification_label": verification_label,
                "official_contributor": listed_commits is not None,
                "contributor_rank": contributor_rank,
                "default_branch_commits": default_branch_commits,
                "contributor_commits": contributor_commits,
                "merged_prs": len(merged),
                "open_prs": len(open_pull_requests),
                "impact_category": impact["primary"],
                "impact_category_prs": impact["primary_prs"],
                "impact_classified_prs": impact["classified_prs"],
                "impact_breakdown": impact["breakdown"],
                "impact_basis": impact["basis"],
                "accepted_additions": sum(int(item["additions"]) for item in merged),
                "accepted_deletions": sum(int(item["deletions"]) for item in merged),
                "accepted_changed_files": sum(
                    int(item["changedFiles"]) for item in merged
                ),
                "stargazers": int(repository["stargazerCount"]),
                "forks": int(repository["forkCount"]),
                "_merged_pull_requests": merged,
                "_open_pull_requests": open_pull_requests,
            }
        )

    repositories.sort(
        key=lambda item: (
            -int(item["merged_prs"]),
            -int(item["contributor_commits"]),
            -int(item["stargazers"]),
            str(item["full_name"]).casefold(),
        )
    )
    return repositories


def discover_owned_repositories(token: str) -> list[dict[str, object]]:
    """Discover public source repositories owned by the profile login."""
    repositories: list[dict[str, object]] = []
    page = 1
    while True:
        parameters = urllib.parse.urlencode(
            {
                "type": "owner",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            }
        )
        payload = rest_json(
            token, f"https://api.github.com/users/{LOGIN}/repos?{parameters}"
        )
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected GitHub owned-repositories response")
        for repository in payload:
            owner = repository.get("owner") or {}
            full_name = str(repository.get("full_name", ""))
            if (
                not full_name
                or str(owner.get("login", "")).casefold() != LOGIN.casefold()
                or bool(repository.get("private"))
                or bool(repository.get("fork"))
                or bool(repository.get("archived"))
                or bool(repository.get("disabled"))
                or full_name.casefold() in OWNED_PROJECT_EXCLUSIONS
            ):
                continue
            commits, _rank = official_contribution_stats(token, full_name)
            commits = commits or 0
            latest_release = discover_latest_release(token, full_name)
            override = OWNED_PRESENTATION_OVERRIDES.get(full_name, {})
            palette_index = hashlib.sha256(full_name.encode()).digest()[0] % len(
                ACCENT_PAIRS
            )
            accent, secondary = ACCENT_PAIRS[palette_index]
            repositories.append(
                {
                    "name": override.get("name", repository["name"]),
                    "full_name": full_name,
                    "url": repository["html_url"],
                    "description": readable_description(repository.get("description")),
                    "homepage": repository.get("homepage") or None,
                    "language": repository.get("language") or "Multi-language",
                    "topics": repository.get("topics") or [],
                    "stargazers": int(repository.get("stargazers_count") or 0),
                    "forks": int(repository.get("forks_count") or 0),
                    "contributor_commits": commits,
                    "pushed_at": repository.get("pushed_at"),
                    "created_at": repository.get("created_at"),
                    "latest_release": latest_release,
                    "ownership": "owner",
                    "visibility": "public",
                    "accent": accent,
                    "secondary": secondary,
                }
            )
        if len(payload) < 100:
            break
        page += 1

    # Stable sorts make the public ranking legible: reach, recent activity,
    # then attributed work. The complete discovered set is still published.
    repositories.sort(key=lambda item: str(item["full_name"]).casefold())
    repositories.sort(
        key=lambda item: int(item["contributor_commits"]), reverse=True
    )
    repositories.sort(
        key=lambda item: str(item.get("pushed_at") or ""), reverse=True
    )
    repositories.sort(key=lambda item: int(item["stargazers"]), reverse=True)
    return repositories


def public_merged_pull_request_activity(
    repositories: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return every accepted public PR as a deduplicated activity record."""
    records: dict[str, dict[str, object]] = {}
    for repository in repositories:
        full_name = str(repository["full_name"])
        for pull_request in repository.get("_merged_pull_requests", []):
            url = str(pull_request["url"])
            records[url] = {
                "repository": full_name,
                "repository_url": str(repository["url"]),
                "number": int(pull_request["number"]),
                "title": str(pull_request["title"]),
                "url": url,
                "created_at": pull_request.get("createdAt"),
                "merged_at": pull_request.get("mergedAt"),
                "additions": int(pull_request["additions"]),
                "deletions": int(pull_request["deletions"]),
                "changed_files": int(pull_request["changedFiles"]),
                "commits": int(pull_request["commits"]["totalCount"]),
                "comments": int(pull_request["comments"]["totalCount"]),
                "reviews": int(pull_request["reviews"]["totalCount"]),
                "attribution": "GitHub-authored and merged pull request",
            }
    return sorted(
        records.values(),
        key=lambda item: (
            str(item.get("merged_at") or ""),
            str(item["repository"]).casefold(),
            int(item["number"]),
        ),
        reverse=True,
    )


def compact(value: int) -> str:
    if value < 1_000:
        return str(value)
    if value < 10_000:
        return f"{value / 1_000:.1f}k"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    return f"{round(value / 1_000):.0f}k"


def counted(value: int, noun: str) -> str:
    """Format a count without visibly broken singular grammar."""
    return f"{value:,} {noun if value == 1 else noun + 's'}"


def owned_project_portfolio_score(
    repo: dict[str, object], updated: str
) -> float:
    """Score public owner projects using current, inspectable GitHub signals."""
    as_of = dt.date.fromisoformat(updated)
    pushed_raw = str(repo.get("pushed_at") or "")[:10]
    try:
        pushed = dt.date.fromisoformat(pushed_raw)
        age_days = max((as_of - pushed).days, 0)
    except ValueError:
        age_days = 365
    recency = max(120 - age_days, 0)
    release = repo.get("latest_release") or {}
    release_published = str(release.get("published_at") or "")[:10]
    release_bonus = 0
    if release_published:
        try:
            release_age = max(
                (as_of - dt.date.fromisoformat(release_published)).days, 0
            )
            release_bonus = max(300 - release_age * 2, 0)
        except ValueError:
            release_bonus = 0
    description = str(repo.get("description") or "").strip()
    topics = repo.get("topics") or []
    return round(
        int(repo.get("stargazers") or 0) * 100
        + int(repo.get("forks") or 0) * 60
        + min(int(repo.get("contributor_commits") or 0), 150) * 1.5
        + recency * 1.5
        + (25 if description and description != "Public source project" else 0)
        + (20 if repo.get("homepage") else 0)
        + min(len(topics), 5) * 4
        + release_bonus,
        1,
    )


def select_featured_owned_projects(
    repositories: list[dict[str, object]],
    updated: str,
    limit: int = FEATURED_OWNED_PROJECT_LIMIT,
) -> list[dict[str, object]]:
    """Re-rank the public frontier while keeping strategic anchors separate."""
    return sorted(
        [
            repo
            for repo in repositories
            if str(repo.get("full_name") or "").casefold()
            != KYA_EVIDENCE_FULL_NAME.casefold()
        ],
        key=lambda repo: (
            -owned_project_portfolio_score(repo, updated),
            str(repo.get("full_name") or repo.get("name") or "").casefold(),
        ),
    )[:limit]


def compact_counted(value: int, noun: str) -> str:
    return f"{compact(value)} {noun if value == 1 else noun + 'S'}"


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


def render_hero_svg(
    repositories: list[dict[str, object]], profile: dict[str, object]
) -> str:
    """Render the first-screen identity and live proof signal."""
    total_merged = sum(int(repo["merged_prs"]) for repo in repositories)
    merged_projects = sum(
        1 for repo in repositories if int(repo.get("merged_prs") or 0)
    )
    total_commits = sum(int(repo["contributor_commits"]) for repo in repositories)
    total_line_changes = sum(
        int(repo["accepted_additions"]) + int(repo["accepted_deletions"])
        for repo in repositories
    )
    total_stars = sum(int(repo["stargazers"]) for repo in repositories)
    agentic_power = float(profile["agentic_power_x"]["base"])
    updated = html.escape(str(profile["updated_at_utc"]))
    return f"""<svg width="1200" height="420" viewBox="0 0 1200 420" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc">
  <title id="title">Rudy Celekli, evidence-first agentic systems</title>
  <desc id="desc">Forward deployed AI researcher and agentic AI engineer building systems that can prove what happened. Live evidence includes {merged_projects} projects with merged pull requests among {len(repositories)} verified upstream repositories, {total_merged} merged pull requests, {total_commits} attributed commits, {total_line_changes:,} accepted line changes, {total_stars} current repository stars, and a provisional Agentic Power estimate of {agentic_power:.1f} times.</desc>
  <defs>
    <style>
      .orbit {{ transform-box: fill-box; transform-origin: center; animation: orbit 20s linear infinite; }}
      .signal {{ stroke-dasharray: 8 10; animation: signal 2.8s linear infinite; }}
      .signal.reverse {{ animation-direction: reverse; }}
      .beacon {{ animation: beacon 3s ease-in-out infinite; }}
      .beacon.delay-1 {{ animation-delay: .75s; }}
      .beacon.delay-2 {{ animation-delay: 1.5s; }}
      .beacon.delay-3 {{ animation-delay: 2.25s; }}
      .verified-core {{ animation: verified 3.2s ease-in-out infinite; }}
      .proof-live {{ animation: proof-live 3.4s ease-in-out infinite; }}
      .hero-kicker, .hero-line, .hero-subtitle, .proof-rail {{ opacity: .35; animation: hero-reveal .9s cubic-bezier(.16,1,.3,1) forwards; }}
      .hero-line-1 {{ animation-delay: .12s; }}
      .hero-line-2 {{ animation-delay: .24s; }}
      .hero-line-3 {{ animation-delay: .36s; }}
      .hero-subtitle {{ animation-delay: .52s; }}
      .proof-rail {{ animation-delay: .68s; }}
      .visual-assembly {{ opacity: .4; animation: visual-reveal 1.1s .24s cubic-bezier(.16,1,.3,1) forwards; }}
      .scan-beam {{ opacity: 0; animation: scan-beam 7s cubic-bezier(.76,0,.24,1) infinite; }}
      .ring-wave {{ transform-box: fill-box; transform-origin: center; animation: ring-wave 4.8s cubic-bezier(.16,1,.3,1) infinite; }}
      .ring-wave.delay {{ animation-delay: 2.4s; }}
      .packet {{ animation: packet-run 2.8s cubic-bezier(.4,0,.2,1) infinite; }}
      .packet.delay-1 {{ animation-delay: .7s; }}
      .packet.delay-2 {{ animation-delay: 1.4s; }}
      .packet.delay-3 {{ animation-delay: 2.1s; }}
      .state-chip {{ animation: state-focus 8s cubic-bezier(.16,1,.3,1) infinite; }}
      .state-chip-2 {{ animation-delay: 2s; }}
      .state-chip-3 {{ animation-delay: 4s; }}
      .state-chip-4 {{ animation-delay: 6s; }}
      .check-draw {{ stroke-dasharray: 42; stroke-dashoffset: 42; animation: check-draw 4.8s cubic-bezier(.16,1,.3,1) infinite; }}
      .border-trace {{ stroke-dasharray: 90 2300; animation: border-trace 11s linear infinite; }}
      @keyframes orbit {{ to {{ transform: rotate(360deg); }} }}
      @keyframes signal {{ to {{ stroke-dashoffset: -36; }} }}
      @keyframes beacon {{ 0%, 100% {{ opacity: .5; }} 50% {{ opacity: 1; }} }}
      @keyframes verified {{ 0%, 100% {{ opacity: .82; }} 50% {{ opacity: 1; }} }}
      @keyframes proof-live {{ 0%, 100% {{ opacity: .72; }} 50% {{ opacity: 1; }} }}
      @keyframes hero-reveal {{ from {{ opacity: .35; transform: translateY(15px); }} to {{ opacity: 1; transform: translateY(0); }} }}
      @keyframes visual-reveal {{ from {{ opacity: .4; }} to {{ opacity: 1; }} }}
      @keyframes scan-beam {{ 0%, 12% {{ opacity: 0; transform: translateX(0); }} 22%, 74% {{ opacity: .62; }} 88%, 100% {{ opacity: 0; transform: translateX(430px); }} }}
      @keyframes ring-wave {{ 0% {{ opacity: .52; transform: scale(.62); }} 72%, 100% {{ opacity: 0; transform: scale(1.45); }} }}
      @keyframes packet-run {{ 0% {{ opacity: 0; transform: translate(0,0); }} 14%, 82% {{ opacity: 1; }} 100% {{ opacity: 0; transform: translate(var(--dx),var(--dy)); }} }}
      @keyframes state-focus {{ 0%, 19%, 100% {{ opacity: .45; }} 5%, 13% {{ opacity: 1; filter: brightness(1.35); }} }}
      @keyframes check-draw {{ 0%, 14% {{ stroke-dashoffset: 42; opacity: .35; }} 32%, 82% {{ stroke-dashoffset: 0; opacity: 1; }} 100% {{ stroke-dashoffset: 0; opacity: .45; }} }}
      @keyframes border-trace {{ to {{ stroke-dashoffset: -2390; }} }}
      @media (prefers-reduced-motion: reduce) {{
        .orbit, .signal, .beacon, .verified-core, .proof-live, .hero-kicker, .hero-line, .hero-subtitle, .proof-rail, .state-chip, .check-draw, .border-trace {{ animation: none; opacity: 1; transform: none; stroke-dashoffset: 0; filter: none; }}
        .visual-assembly {{ animation: none; opacity: 1; }}
        .scan-beam, .ring-wave, .packet {{ display: none; }}
      }}
    </style>
    <linearGradient id="hero-bg" x1="0" y1="0" x2="1200" y2="420" gradientUnits="userSpaceOnUse">
      <stop stop-color="#07111E"/>
      <stop offset="0.55" stop-color="#0A1020"/>
      <stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="hero-accent" x1="735" y1="80" x2="1100" y2="350" gradientUnits="userSpaceOnUse">
      <stop stop-color="#2DE2C5"/>
      <stop offset="0.55" stop-color="#38BDF8"/>
      <stop offset="1" stop-color="#8B5CF6"/>
    </linearGradient>
    <radialGradient id="hero-glow" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(946 211) rotate(90) scale(188)">
      <stop stop-color="#2DE2C5" stop-opacity="0.18"/>
      <stop offset="1" stop-color="#2DE2C5" stop-opacity="0"/>
    </radialGradient>
    <pattern id="hero-grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" stroke="#94A3B8" stroke-opacity="0.07"/>
    </pattern>
    <filter id="hero-soft-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="5" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="hero-visual-clip"><rect x="686" y="34" width="472" height="344" rx="24"/></clipPath>
  </defs>

  <rect width="1200" height="420" rx="28" fill="url(#hero-bg)"/>
  <rect x="1" y="1" width="1198" height="418" rx="27" stroke="#F8FAFC" stroke-opacity="0.08" stroke-width="2"/>
  <rect class="border-trace" x="2" y="2" width="1196" height="416" rx="26" stroke="url(#hero-accent)" stroke-opacity=".46" stroke-width="2"/>
  <rect width="1200" height="420" rx="28" fill="url(#hero-grid)"/>
  <circle cx="946" cy="211" r="188" fill="url(#hero-glow)"/>
  <g clip-path="url(#hero-visual-clip)"><rect class="scan-beam" x="690" y="46" width="1.5" height="320" fill="#7FFFEA"/><rect class="scan-beam" x="691.5" y="46" width="34" height="320" fill="#2DE2C5" fill-opacity=".035"/></g>

  <g font-family="ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace">
    <text class="hero-kicker" x="72" y="64" fill="#2DE2C5" font-size="13" font-weight="700" letter-spacing="3">RUDY CELEKLI / EVIDENCE-FIRST AGENTIC SYSTEMS</text>
    <text class="hero-line hero-line-1" x="72" y="138" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="48" font-weight="780" letter-spacing="-1.6">I build agentic systems</text>
    <text class="hero-line hero-line-2" x="72" y="195" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="48" font-weight="780" letter-spacing="-1.6">that can prove</text>
    <text class="hero-line hero-line-3" x="72" y="252" fill="#2DE2C5" font-family="Avenir Next,Segoe UI,sans-serif" font-size="48" font-weight="780" letter-spacing="-1.6">what happened.</text>
    <text class="hero-subtitle" x="72" y="304" fill="#AAB8CA" font-family="Avenir Next,Segoe UI,sans-serif" font-size="14">Forward deployed AI researcher · Agentic AI engineer · Enterprise AI</text>
    <g class="proof-rail"><line x1="72" y1="333" x2="664" y2="333" stroke="#26364D"/>
    <circle class="proof-live" cx="78" cy="358" r="5" fill="#2DE2C5"/>
    <text x="94" y="362" fill="#91A2B8" font-size="10.5" letter-spacing=".8">LIVE PROOF · {merged_projects} WITH MERGED PRS / {len(repositories)} VERIFIED · {total_merged:,} MERGED PRS · {compact(total_commits)} ATTRIBUTED COMMITS</text>
    <text x="94" y="384" fill="#91A2B8" font-size="10.5" letter-spacing=".8">{total_line_changes:,} ACCEPTED LINE CHANGES · {compact(total_stars)} CURRENT REPO REACH · {agentic_power:.1f}× PROVISIONAL AP</text>
    <text x="664" y="405" fill="#65758B" font-size="9.5" letter-spacing="1" text-anchor="end">MACHINE-COUNTED · {updated} UTC</text></g>
  </g>

  <g class="visual-assembly" transform="translate(940 210)">
    <circle r="137" stroke="#38BDF8" stroke-opacity="0.12"/>
    <circle class="ring-wave" r="76" stroke="#2DE2C5" stroke-opacity=".42"/>
    <circle class="ring-wave delay" r="76" stroke="#8B5CF6" stroke-opacity=".34"/>
    <circle class="orbit" r="102" stroke="#2DE2C5" stroke-opacity="0.42" stroke-dasharray="5 9"/>
    <circle r="66" stroke="url(#hero-accent)" stroke-width="2" stroke-opacity="0.55"/>
    <circle class="verified-core" r="30" fill="#0B1627" stroke="#2DE2C5" stroke-width="2" filter="url(#hero-soft-glow)"/>
    <path class="check-draw" d="M-12 0L-3 9L15 -12" stroke="#E6FFFA" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>

    <path class="signal" d="M-174 -62H-120L-87 -34" stroke="#2DE2C5" stroke-width="2"/>
    <circle class="beacon" cx="-174" cy="-62" r="5" fill="#2DE2C5"/>
    <circle class="packet" style="--dx:87px;--dy:28px" cx="-174" cy="-62" r="4" fill="#B8FFF4"/>
    <path class="signal reverse" d="M-172 85H-118L-86 48" stroke="#38BDF8" stroke-width="2"/>
    <circle class="beacon delay-1" cx="-172" cy="85" r="5" fill="#38BDF8"/>
    <circle class="packet delay-1" style="--dx:86px;--dy:-37px" cx="-172" cy="85" r="4" fill="#BAE6FD"/>
    <path class="signal" d="M169 -84H123L90 -48" stroke="#8B5CF6" stroke-width="2"/>
    <circle class="beacon delay-2" cx="169" cy="-84" r="5" fill="#8B5CF6"/>
    <circle class="packet delay-2" style="--dx:-79px;--dy:36px" cx="169" cy="-84" r="4" fill="#DDD6FE"/>
    <path class="signal reverse" d="M177 72H122L91 43" stroke="#F2A93B" stroke-width="2"/>
    <circle class="beacon delay-3" cx="177" cy="72" r="5" fill="#F2A93B"/>
    <circle class="packet delay-3" style="--dx:-86px;--dy:-29px" cx="177" cy="72" r="4" fill="#FDE68A"/>
  </g>

  <g font-family="ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace" font-size="11" font-weight="700" letter-spacing="1.3">
    <g class="state-chip state-chip-1" transform="translate(722 128)"><rect width="116" height="28" rx="14" fill="#0B1D2A" stroke="#2DE2C5" stroke-opacity="0.4"/><text x="58" y="18" text-anchor="middle" fill="#7FFFEA">OBSERVED</text></g>
    <g class="state-chip state-chip-2" transform="translate(718 278)"><rect width="116" height="28" rx="14" fill="#0B1B2E" stroke="#38BDF8" stroke-opacity="0.4"/><text x="58" y="18" text-anchor="middle" fill="#8ADFFF">RECORDED</text></g>
    <g class="state-chip state-chip-3" transform="translate(1041 106)"><rect width="108" height="28" rx="14" fill="#17102A" stroke="#8B5CF6" stroke-opacity="0.45"/><text x="54" y="18" text-anchor="middle" fill="#C4B5FD">REPLAYED</text></g>
    <g class="state-chip state-chip-4" transform="translate(1048 264)"><rect width="102" height="28" rx="14" fill="#24180B" stroke="#F2A93B" stroke-opacity="0.45"/><text x="51" y="18" text-anchor="middle" fill="#F8C66E">VERIFIED</text></g>
  </g>
</svg>
"""


def render_building_now_svg(
    owned_repositories: list[dict[str, object]], updated: str
) -> str:
    """Render equal strategic anchors and the rotating auto-ranked frontier."""
    featured = select_featured_owned_projects(owned_repositories, updated)
    kya_repo = next(
        (
            repo
            for repo in owned_repositories
            if str(repo.get("full_name") or "").casefold()
            == KYA_EVIDENCE_FULL_NAME.casefold()
        ),
        None,
    )
    kya_activity = (
        f"{compact_counted(int(kya_repo['contributor_commits']), 'COMMIT')} · "
        f"{compact_counted(int(kya_repo['stargazers']), 'STAR')}"
        if kya_repo
        else "PUBLIC ALPHA · OPEN PROTOCOL"
    )
    frontier_pages: list[str] = []
    page_count = max(
        1,
        (len(featured) + BUILDING_FRONTIER_PAGE_SIZE - 1)
        // BUILDING_FRONTIER_PAGE_SIZE,
    )
    for page_index in range(page_count):
        page_repositories = featured[
            page_index
            * BUILDING_FRONTIER_PAGE_SIZE : (page_index + 1)
            * BUILDING_FRONTIER_PAGE_SIZE
        ]
        rows: list[str] = []
        for row_index, repo in enumerate(page_repositories):
            rank = page_index * BUILDING_FRONTIER_PAGE_SIZE + row_index + 1
            y = 36 + row_index * 82
            description = str(repo.get("description") or "Public source project")
            description = description.replace("—", "·").replace("–", "-")
            description_lines = wrap_svg_text(description, width=61, lines=2)
            description_svg = "".join(
                f'<text x="66" y="{y + 38 + line_index * 16}" fill="#91A2B8" font-size="11.5">{html.escape(line)}</text>'
                for line_index, line in enumerate(description_lines)
            )
            pushed = str(repo.get("pushed_at") or "")[:10] or "unknown"
            release = repo.get("latest_release") or {}
            if release:
                release_kind = (
                    "PRE-RELEASE" if release.get("prerelease") else "RELEASE"
                )
                activity = (
                    f"{ellipsize(release.get('tag_name') or 'release', 14)} "
                    f"{release_kind}"
                )
                activity_color = str(repo["accent"])
            else:
                activity = compact_counted(
                    int(repo["contributor_commits"]), "COMMIT"
                )
                activity_color = "#AAB8CA"
            rows.append(
                f"""
      <g class="frontier-row frontier-row-{row_index + 1}">
        <text x="0" y="{y + 25}" fill="{repo['accent']}" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" font-weight="700">{rank:02d}</text>
        <text x="66" y="{y + 24}" fill="#F8FAFC" font-size="20" font-weight="780">{html.escape(ellipsize(repo['name'], 24))}</text>
        <text x="642" y="{y + 22}" fill="{activity_color}" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5" text-anchor="end">{html.escape(activity)} · {compact_counted(int(repo['stargazers']), 'STAR')} · {pushed}</text>
        {description_svg}
        <line x1="0" y1="{y + 72}" x2="642" y2="{y + 72}" stroke="#203149"/>
      </g>"""
            )
        page_class = (
            f"frontier-page frontier-page-{page_index + 1}"
            if page_count > 1
            else "frontier-page-static"
        )
        frontier_pages.append(
            f"""
    <g class="{page_class}">
      <text x="642" y="14" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5" text-anchor="end">VIEW {page_index + 1} / {page_count}</text>
      {''.join(rows)}
    </g>"""
        )
    project_names = ", ".join(str(repo["name"]) for repo in featured)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="410" viewBox="0 0 1200 410" role="img" aria-labelledby="title desc">
  <title id="title">What Rudy Celekli is building now</title>
  <desc id="desc">Gradia and KYA Evidence are equal strategic anchors. Gradia is an AI Research and Implementation Lab building a verified software factory on its existing research and evaluation foundation. KYA Evidence proposes portable, scope-bound agent assurance with inspectable evidence. The automatically ranked public frontier currently features {html.escape(project_names)} using repository reach, recent shipping, public releases, attributed work, and project completeness.</desc>
  <defs>
    <style>
      .private-signal {{ stroke-dasharray: 7 10; animation: building-flow 2.8s linear infinite; }}
      .private-orbit {{ transform-box: fill-box; transform-origin: center; animation: private-orbit 18s linear infinite; }}
      .private-pulse {{ transform-box: fill-box; transform-origin: center; animation: private-pulse 4.6s cubic-bezier(.16,1,.3,1) infinite; }}
      .private-pulse.delay {{ animation-delay: 2.3s; }}
      .frontier-page {{ opacity: 0; }}
      .frontier-page-1 {{ animation: frontier-page-one 16s cubic-bezier(.16,1,.3,1) infinite; }}
      .frontier-page-2 {{ animation: frontier-page-two 16s cubic-bezier(.16,1,.3,1) infinite; }}
      .frontier-progress {{ transform-box: fill-box; transform-origin: left; animation: frontier-progress 8s linear infinite; }}
      @keyframes building-flow {{ to {{ stroke-dashoffset: -34; }} }}
      @keyframes private-orbit {{ to {{ transform: rotate(360deg); }} }}
      @keyframes private-pulse {{ 0% {{ opacity: .46; transform: scale(.65); }} 72%, 100% {{ opacity: 0; transform: scale(1.35); }} }}
      @keyframes frontier-page-one {{ 0%, 43% {{ opacity: 1; transform: translateY(0); }} 48%, 95% {{ opacity: 0; transform: translateY(-12px); }} 100% {{ opacity: 1; transform: translateY(0); }} }}
      @keyframes frontier-page-two {{ 0%, 43% {{ opacity: 0; transform: translateY(12px); }} 48%, 95% {{ opacity: 1; transform: translateY(0); }} 100% {{ opacity: 0; transform: translateY(-12px); }} }}
      @keyframes frontier-progress {{ from {{ transform: scaleX(0); }} to {{ transform: scaleX(1); }} }}
      @media (prefers-reduced-motion: reduce) {{
        .private-signal, .private-orbit, .private-pulse, .frontier-page, .frontier-row, .frontier-progress {{ animation: none; opacity: 1; transform: none; }}
        .private-pulse {{ display: none; }}
        .frontier-page-1 {{ opacity: 1; }}
        .frontier-page-2 {{ opacity: 0; }}
      }}
    </style>
    <linearGradient id="building-canvas" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#07111E"/>
      <stop offset="0.58" stop-color="#0B1728"/>
      <stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="building-rule" x1="0" y1="0" x2="1" y2="0">
      <stop stop-color="#2DE2C5"/>
      <stop offset="0.52" stop-color="#4D7CFE"/>
      <stop offset="1" stop-color="#9B7CFF"/>
    </linearGradient>
    <radialGradient id="building-glow">
      <stop stop-color="#2DE2C5" stop-opacity="0.18"/>
      <stop offset="1" stop-color="#2DE2C5" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="410" rx="28" fill="url(#building-canvas)"/>
  <rect x="1" y="1" width="1198" height="408" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="30" width="66" height="4" rx="2" fill="url(#building-rule)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="27" font-weight="780">BUILDING, NOW</text>
  <text x="1158" y="65" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11" text-anchor="end">STRATEGIC ANCHORS + AUTO-RANKED PUBLIC FRONTIER · {html.escape(updated)} UTC</text>

  <g transform="translate(42 103)" font-family="Avenir Next,Segoe UI,sans-serif">
    <g>
      <rect width="410" height="117" rx="20" fill="#0A2026" stroke="#2DE2C5" stroke-opacity="0.54"/>
      <circle cx="348" cy="58" r="72" fill="url(#building-glow)"/>
      <text x="22" y="27" fill="#6EE7D8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5" letter-spacing="1.35">STRATEGIC ANCHOR · AI LAB</text>
      <text x="22" y="62" fill="#F8FAFC" font-size="29" font-weight="830">Gradia</text>
      <text x="22" y="84" fill="#D6E2EE" font-size="12.5" font-weight="650">Research, build, verify and implement real workflows.</text>
      <text x="22" y="104" fill="#2DE2C5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5">VERIFIED SOFTWARE FACTORY · IN DEVELOPMENT</text>
      <circle class="private-pulse" cx="354" cy="57" r="31" fill="none" stroke="#2DE2C5" stroke-opacity=".42"/>
      <circle class="private-orbit" cx="354" cy="57" r="25" fill="none" stroke="#7FFFEA" stroke-opacity=".34" stroke-dasharray="4 8"/>
      <path class="private-signal" d="M324 42 H363 V76 H337" fill="none" stroke="#2DE2C5" stroke-width="1.5"/>
    </g>
    <g transform="translate(0 129)">
      <rect width="410" height="117" rx="20" fill="#141329" stroke="#9B7CFF" stroke-opacity="0.62"/>
      <text x="22" y="27" fill="#C4B5FD" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5" letter-spacing="1.35">STRATEGIC ANCHOR · PUBLIC PROTOCOL</text>
      <text x="22" y="62" fill="#F8FAFC" font-size="29" font-weight="830">KYA Evidence</text>
      <text x="22" y="84" fill="#D6E2EE" font-size="12.5" font-weight="650">Know Your Agent: exact scope, evidence, policy.</text>
      <text x="22" y="104" fill="#9B7CFF" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5">{html.escape(kya_activity)} · EXPERIMENTAL REFERENCE</text>
      <circle class="private-pulse delay" cx="354" cy="57" r="31" fill="none" stroke="#9B7CFF" stroke-opacity=".44"/>
      <circle class="private-orbit" cx="354" cy="57" r="25" fill="none" stroke="#C4B5FD" stroke-opacity=".42" stroke-dasharray="3 7"/>
      <path class="private-signal" d="M334 57 L346 69 L373 42" fill="none" stroke="#C4B5FD" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    </g>
  </g>

  <g transform="translate(494 103)" font-family="Avenir Next,Segoe UI,sans-serif">
    <text x="0" y="14" fill="#9B7CFF" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11" letter-spacing="1.6">PUBLIC FRONTIER / {len(featured)} AUTO-RANKED</text>
    {''.join(frontier_pages)}
  </g>

  <text x="42" y="383" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5">SIGNALS: REACH · SHIP RECENCY · RELEASES · ATTRIBUTED WORK · COMPLETENESS</text>
  <text x="1158" y="383" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5" text-anchor="end">2 ANCHORS · {len(featured)} FRONTIER · ROTATES 8 SEC · REFRESH 30 MIN</text>
  <rect x="494" y="396" width="664" height="2" rx="1" fill="#203149"/><rect class="frontier-progress" x="494" y="396" width="664" height="2" rx="1" fill="url(#building-rule)"/>
</svg>
"""


def render_building_now_readme(
    owned_repositories: list[dict[str, object]], updated: str
) -> str:
    """Render three flagship systems and a compact auto-ranked frontier."""
    featured = select_featured_owned_projects(owned_repositories, updated)
    frontier = featured[:4]
    frontier_links = " · ".join(
        f"[{repo['name']}]({repo['url']})" for repo in frontier
    )
    lines = [
        "<!-- building-now:start -->",
        "## Three systems, one research program",
        "",
        "| System | Problem | Inspectable response | Maturity |",
        "|:--|:--|:--|:--|",
        "| **[Gradia](https://www.gradiahq.com)** | AI implementation is difficult to reproduce, inspect, and improve across long-running work. | A research and implementation lab connecting **Blocks · Factory · Verify · Workers · Intelligence**, with [AxiomConsulting.ai](https://axiomconsulting.ai/) as its forward-deployed implementation arm. | Research foundation established; broader verified-software-factory products remain in development. |",
        "| **[KYA Evidence](https://github.com/rudycelekli/kya-evidence)** | Agent identity and capability claims are usually broader than their evidence. | A portable, scope-bound Know Your Agent protocol that binds the exact system, evidence, and relying-party policy. | Experimental public reference implementation and standardization path, not an adopted industry standard. |",
        "| **Proof stack** | Agent output can look plausible while its execution record, evaluator, or regression boundary is wrong. | [Gradia Guard](https://github.com/rudycelekli/gradia-guard) records proof-bound execution, [ProofSeal](https://github.com/rudycelekli/proofseal) retains behavioral claims, and [TestLore](https://github.com/rudycelekli/testlore) turns testing history into reusable quality intelligence. | Public source with linked code, releases, and explicit claim boundaries. |",
        "",
        f"**Current public frontier:** {frontier_links}",
        "",
        f"<sub>Frontier selections are re-ranked every 30 minutes from public reach, shipping recency, releases, attributed work, and repository completeness · last ranked {updated} UTC · private Gradia repository evidence remains private</sub>",
        "<!-- building-now:end -->",
    ]
    return "\n".join(lines)


def top_five_contributor_repositories(
    repositories: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Select only projects where GitHub currently ranks the login 1 through 5."""
    qualified = [
        repo
        for repo in repositories
        if repo.get("contributor_rank") is not None
        and 1 <= int(repo["contributor_rank"]) <= 5
    ]
    return sorted(
        qualified,
        key=lambda repo: (
            int(repo["contributor_rank"]),
            -int(repo["stargazers"]),
            str(repo["full_name"]).casefold(),
        ),
    )


def render_contributor_authority_svg(
    repositories: list[dict[str, object]], updated: str
) -> str:
    """Render a compact authority strip without turning proof into a link wall."""
    qualified = top_five_contributor_repositories(repositories)
    rank_counts = {
        rank: sum(1 for repo in qualified if int(repo["contributor_rank"]) == rank)
        for rank in range(1, 6)
    }
    combined_stars = sum(int(repo.get("stargazers") or 0) for repo in qualified)
    selected = qualified[:6]
    project_cells = []
    for index, repo in enumerate(selected):
        x = 42 + (index % 3) * 372
        y = 178 + (index // 3) * 43
        project_cells.append(
            f'<g transform="translate({x} {y})">'
            f'<text fill="#F8FAFC" font-size="13" font-weight="720">{html.escape(ellipsize(str(repo["name"]), 24))}</text>'
            f'<text x="250" fill="#2DE2C5" font-size="11" text-anchor="end">#{int(repo["contributor_rank"])}</text>'
            f'<text x="312" fill="#91A2B8" font-size="10" text-anchor="end">{compact(int(repo["stargazers"]))} ★</text>'
            f'</g>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="290" viewBox="0 0 1200 290" role="img" aria-labelledby="authority-title authority-desc">
  <title id="authority-title">GitHub contributor authority for Rudy Celekli</title>
  <desc id="authority-desc">GitHub currently lists Rudy in the all-time top five contributors for {len(qualified)} public repositories: {rank_counts[1]} at rank one, {rank_counts[2]} at rank two, {rank_counts[3]} at rank three, {rank_counts[4]} at rank four, and {rank_counts[5]} at rank five. Repository stars indicate current reach, not personal credit.</desc>
  <defs>
    <linearGradient id="authority-canvas" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#07111E"/><stop offset="1" stop-color="#150B2E"/></linearGradient>
    <linearGradient id="authority-rule" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#2DE2C5"/><stop offset=".52" stop-color="#4D7CFE"/><stop offset="1" stop-color="#9B7CFF"/></linearGradient>
  </defs>
  <rect width="1200" height="290" rx="28" fill="url(#authority-canvas)"/>
  <rect x="1" y="1" width="1198" height="288" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="30" width="76" height="4" rx="2" fill="url(#authority-rule)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="27" font-weight="780">UPSTREAM AUTHORITY</text>
  <text x="1158" y="66" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11.5" text-anchor="end">GITHUB CONTRIBUTORS API · {html.escape(updated)} UTC</text>
  <g transform="translate(42 108)" font-family="ui-monospace,SFMono-Regular,monospace">
    <text fill="#2DE2C5" font-size="32" font-weight="780">{len(qualified)}</text><text y="22" fill="#91A2B8" font-size="9.5">TOP-5 POSITIONS</text>
    <text x="215" fill="#F8FAFC" font-size="32" font-weight="780">{rank_counts[1]}</text><text x="215" y="22" fill="#91A2B8" font-size="9.5">AT #1</text>
    <text x="392" fill="#9B7CFF" font-size="32" font-weight="780">{rank_counts[2]}</text><text x="392" y="22" fill="#91A2B8" font-size="9.5">AT #2</text>
    <text x="569" fill="#4D7CFE" font-size="32" font-weight="780">{rank_counts[3]}</text><text x="569" y="22" fill="#91A2B8" font-size="9.5">AT #3</text>
    <text x="746" fill="#F2A93B" font-size="32" font-weight="780">{rank_counts[4] + rank_counts[5]}</text><text x="746" y="22" fill="#91A2B8" font-size="9.5">AT #4–5</text>
    <text x="962" fill="#F8FAFC" font-size="32" font-weight="780">{compact(combined_stars)}</text><text x="962" y="22" fill="#91A2B8" font-size="9.5">COMBINED REPO REACH</text>
  </g>
  <line x1="42" y1="157" x2="1158" y2="157" stroke="#203149"/>
  <g font-family="ui-monospace,SFMono-Regular,monospace">{''.join(project_cells)}</g>
  <text x="1158" y="270" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5" text-anchor="end">STARS = REPOSITORY CONTEXT, NOT PERSONAL CREDIT</text>
</svg>
"""


def render_top_contributor_readme(
    repositories: list[dict[str, object]], updated: str
) -> str:
    """Render compact, API-backed all-time contributor-rank proof near the top."""
    qualified = top_five_contributor_repositories(repositories)
    selected = qualified[:6]
    lines = [
        "<!-- top-contributor-proof:start -->",
        "## Upstream authority",
        "",
        '<img src="./assets/contributor-authority.svg" width="100%" alt="GitHub Contributors API authority summary with Top-5 contributor positions and selected repositories" />',
        "",
    ]
    if qualified:
        proof = " · ".join(
            f"[**{repo['name']}**]({repo['url']}) `#{int(repo['contributor_rank'])}` · {compact(int(repo['stargazers']))} ★"
            for repo in selected
        )
        lines.extend(
            [
                f"**Selected proof:** {proof}",
                "",
                f"[Inspect all {len(qualified)} Top-5 positions and their GitHub evidence →](https://rudycelekli.github.io/contributions.html?sort=rank&top5=1)",
            ]
        )
    else:
        lines.append("Contributor-rank evidence is awaiting GitHub's index.")
    lines.extend(
        [
            "",
            f"<sub>All-time rank from GitHub's Contributors API · repository stars are reach, not personal credit · refreshed {updated} UTC</sub>",
            "<!-- top-contributor-proof:end -->",
        ]
    )
    return "\n".join(lines)


def render_latest_verified_work_svg(
    merged_activity: list[dict[str, object]],
    owned_repositories: list[dict[str, object]],
    repositories: list[dict[str, object]],
    updated: str,
) -> str:
    """Render the newest accepted contribution, release, and owned activity."""
    latest_merged = merged_activity[0] if merged_activity else {}
    releases = [
        (repo, repo.get("latest_release") or {})
        for repo in owned_repositories
        if (repo.get("latest_release") or {}).get("published_at")
    ]
    releases.sort(
        key=lambda item: str(item[1].get("published_at") or ""), reverse=True
    )
    release_repo, latest_release = releases[0] if releases else ({}, {})
    latest_owned = max(
        owned_repositories,
        key=lambda repo: str(repo.get("pushed_at") or ""),
        default={},
    )

    merged_title = (
        f"{latest_merged.get('repository', 'Awaiting evidence')} #{latest_merged.get('number', '')} · "
        f"{latest_merged.get('title', 'No accepted upstream receipt yet')}"
    ).strip()
    merged_lines = wrap_svg_text(merged_title, width=70, lines=2)
    merged_date = str(latest_merged.get("merged_at") or "")[:10] or "pending"
    merged_delta = int(latest_merged.get("additions") or 0) + int(
        latest_merged.get("deletions") or 0
    )
    release_title = (
        f"{release_repo.get('name', 'Awaiting release')} · "
        f"{latest_release.get('tag_name') or 'No public release yet'}"
    )
    release_date = str(latest_release.get("published_at") or "")[:10] or "pending"
    owned_title = str(latest_owned.get("name") or "Awaiting owned-project activity")
    owned_date = str(latest_owned.get("pushed_at") or "")[:10] or "pending"
    owned_context = (
        f"{compact(int(latest_owned.get('contributor_commits') or 0))} indexed commits · "
        f"{compact(int(latest_owned.get('stargazers') or 0))} stars"
    )
    merged_projects = sum(1 for repo in repositories if int(repo.get("merged_prs") or 0))
    total_merged = sum(int(repo.get("merged_prs") or 0) for repo in repositories)

    merged_text = "".join(
        f'<text x="113" y="{151 + index * 17}" fill="#F8FAFC" font-size="12.5" font-weight="680">{html.escape(line)}</text>'
        for index, line in enumerate(merged_lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="365" viewBox="0 0 1200 365" role="img" aria-labelledby="latest-title latest-desc">
  <title id="latest-title">Latest verified work by Rudy Celekli</title>
  <desc id="latest-desc">The newest accepted upstream pull request, public owned-project release, and owned-project activity, refreshed from GitHub every 30 minutes.</desc>
  <defs>
    <style>
      .latest-node {{ animation: latest-pulse 4.8s ease-in-out infinite; }}
      .latest-node.delay-1 {{ animation-delay: .8s; }}
      .latest-node.delay-2 {{ animation-delay: 1.6s; }}
      .latest-scan {{ animation: latest-scan 12s linear infinite; }}
      @keyframes latest-pulse {{ 0%,100% {{ opacity:.42; }} 50% {{ opacity:1; }} }}
      @keyframes latest-scan {{ from {{ transform:translateY(0); }} to {{ transform:translateY(204px); }} }}
      @media (prefers-reduced-motion: reduce) {{ .latest-node, .latest-scan {{ animation:none !important; }} .latest-node {{ opacity:1; }} .latest-scan {{ display:none; }} }}
    </style>
    <linearGradient id="latest-canvas" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#07111E"/><stop offset="1" stop-color="#150B2E"/></linearGradient>
    <linearGradient id="latest-rule" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#2DE2C5"/><stop offset=".52" stop-color="#4D7CFE"/><stop offset="1" stop-color="#9B7CFF"/></linearGradient>
  </defs>
  <rect width="1200" height="365" rx="28" fill="url(#latest-canvas)"/>
  <rect x="1" y="1" width="1198" height="363" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="30" width="76" height="4" rx="2" fill="url(#latest-rule)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="27" font-weight="780">LATEST VERIFIED WORK</text>
  <text x="1158" y="66" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11.5" text-anchor="end">GITHUB · {html.escape(updated)} UTC · REFRESH 30 MIN</text>
  <line x1="76" y1="121" x2="76" y2="307" stroke="#203149" stroke-width="2"/>
  <rect class="latest-scan" x="72" y="112" width="8" height="24" rx="4" fill="#2DE2C5" opacity=".26"/>
  <g font-family="ui-monospace,SFMono-Regular,monospace">
    <circle class="latest-node" cx="76" cy="143" r="7" fill="#2DE2C5"/>
    <text x="113" y="128" fill="#2DE2C5" font-size="9.5" letter-spacing="1.1">NEWEST ACCEPTED UPSTREAM</text>
    {merged_text}
    <text x="113" y="190" fill="#65758B" font-size="9.5">MERGED {merged_date} · {merged_delta:,} ACCEPTED LINE CHANGES</text>

    <circle class="latest-node delay-1" cx="76" cy="231" r="7" fill="#9B7CFF"/>
    <text x="113" y="216" fill="#9B7CFF" font-size="9.5" letter-spacing="1.1">LATEST PUBLIC RELEASE</text>
    <text x="113" y="241" fill="#F8FAFC" font-size="13" font-weight="680">{html.escape(ellipsize(release_title, 72))}</text>
    <text x="113" y="260" fill="#65758B" font-size="9.5">PUBLISHED {release_date}</text>

    <circle class="latest-node delay-2" cx="76" cy="303" r="7" fill="#4D7CFE"/>
    <text x="113" y="288" fill="#4D7CFE" font-size="9.5" letter-spacing="1.1">LATEST OWNED-PROJECT ACTIVITY</text>
    <text x="113" y="313" fill="#F8FAFC" font-size="13" font-weight="680">{html.escape(ellipsize(owned_title, 46))}</text>
    <text x="470" y="313" fill="#65758B" font-size="9.5">PUSHED {owned_date} · {html.escape(owned_context)}</text>
  </g>
  <line x1="824" y1="111" x2="824" y2="324" stroke="#203149"/>
  <g transform="translate(866 139)" font-family="ui-monospace,SFMono-Regular,monospace">
    <text fill="#2DE2C5" font-size="31" font-weight="780">{len(repositories)}</text><text y="21" fill="#91A2B8" font-size="9.5">VERIFIED PROJECTS</text>
    <text y="79" fill="#9B7CFF" font-size="31" font-weight="780">{compact(total_merged)}</text><text y="100" fill="#91A2B8" font-size="9.5">MERGED PRS</text>
    <text y="158" fill="#F2A93B" font-size="31" font-weight="780">{merged_projects}</text><text y="179" fill="#91A2B8" font-size="9.5">PROJECTS WITH MERGED WORK</text>
  </g>
</svg>
"""


def render_profile_walkthrough_svg(
    repositories: list[dict[str, object]],
    owned_repositories: list[dict[str, object]],
    profile: dict[str, object],
) -> str:
    """Render a concise animated profile tour backed by live evidence."""
    total_merged = sum(int(repo["merged_prs"]) for repo in repositories)
    total_line_changes = sum(
        int(repo["accepted_additions"]) + int(repo["accepted_deletions"])
        for repo in repositories
    )
    agentic_power = float(profile["agentic_power_x"]["base"])
    updated = html.escape(str(profile["updated_at_utc"]))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="620" viewBox="0 0 1200 620" role="img" aria-labelledby="title desc">
  <title id="title">Rudy Celekli, 35-second operating brief</title>
  <desc id="desc">A seven-chapter animated walkthrough of Rudy's evidence-first agentic engineering practice. Live proof includes {len(repositories)} verified upstream projects, {total_merged} merged pull requests, {total_line_changes:,} accepted line changes, {len(owned_repositories)} owned public projects, and a provisional Agentic Power estimate of {agentic_power:.1f} times.</desc>
  <defs>
    <style>
      .scene {{ opacity: 0; animation: chapter 35s linear infinite; }}
      .scene-1 {{ animation-delay: 0s; }}
      .scene-2 {{ animation-delay: 5s; }}
      .scene-3 {{ animation-delay: 10s; }}
      .scene-4 {{ animation-delay: 15s; }}
      .scene-5 {{ animation-delay: 20s; }}
      .scene-6 {{ animation-delay: 25s; }}
      .scene-7 {{ animation-delay: 30s; }}
      .trace {{ stroke-dasharray: 8 12; animation: trace-flow 2.2s linear infinite; }}
      .pulse {{ animation: signal-pulse 2.8s ease-in-out infinite; }}
      .walk-scan {{ opacity: 0; animation: walk-scan 9s cubic-bezier(.76,0,.24,1) infinite; }}
      .chapter-fill {{ transform-box: fill-box; transform-origin: left; animation: chapter-fill 5s linear infinite; }}
      .chapter-number {{ animation: chapter-number 5s cubic-bezier(.16,1,.3,1) infinite; }}
      .scene-static {{ opacity: 0; }}
      @keyframes chapter {{ 0%, 11.8% {{ opacity: 1; transform: translateY(0); }} 14.285%, 100% {{ opacity: 0; transform: translateY(-10px); }} }}
      @keyframes trace-flow {{ to {{ stroke-dashoffset: -40; }} }}
      @keyframes signal-pulse {{ 0%, 100% {{ opacity: .58; }} 50% {{ opacity: 1; }} }}
      @keyframes walk-scan {{ 0%, 10% {{ opacity: 0; transform: translateX(0); }} 18%, 72% {{ opacity: .46; }} 88%, 100% {{ opacity: 0; transform: translateX(1116px); }} }}
      @keyframes chapter-fill {{ from {{ transform: scaleX(0); }} to {{ transform: scaleX(1); }} }}
      @keyframes chapter-number {{ 0%, 12% {{ opacity: .32; }} 22%, 84% {{ opacity: 1; }} 100% {{ opacity: .32; }} }}
      @media (prefers-reduced-motion: reduce) {{
        .scene, .trace, .pulse, .chapter-fill, .chapter-number {{ animation: none; transform: none; }}
        .walk-scan {{ display: none; }}
        .scene {{ opacity: 0; }}
        .scene-static {{ opacity: 1; }}
      }}
    </style>
    <linearGradient id="walkthrough-canvas" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#07111E"/>
      <stop offset="0.58" stop-color="#0B1728"/>
      <stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="walkthrough-rule" x1="0" y1="0" x2="1" y2="0">
      <stop stop-color="#2DE2C5"/>
      <stop offset="0.52" stop-color="#4D7CFE"/>
      <stop offset="1" stop-color="#9B7CFF"/>
    </linearGradient>
    <radialGradient id="walkthrough-glow">
      <stop stop-color="#4D7CFE" stop-opacity="0.24"/>
      <stop offset="1" stop-color="#4D7CFE" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1200" height="620" rx="28" fill="url(#walkthrough-canvas)"/>
  <rect x="1" y="1" width="1198" height="618" rx="27" fill="none" stroke="#26364D"/>
  <circle cx="955" cy="285" r="280" fill="url(#walkthrough-glow)"/>
  <rect class="walk-scan" x="42" y="95" width="1.5" height="446" fill="#7FFFEA"/><rect class="walk-scan" x="43.5" y="95" width="42" height="446" fill="#4D7CFE" fill-opacity=".025"/>
  <rect x="42" y="34" width="74" height="4" rx="2" fill="url(#walkthrough-rule)"/>
  <text x="42" y="70" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="25" font-weight="780">RUDY CELEKLI / OPERATING BRIEF</text>
  <text x="1158" y="68" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11" text-anchor="end">07 CHAPTERS · 35 SEC · LIVE EVIDENCE {updated}</text>
  <line x1="42" y1="92" x2="1158" y2="92" stroke="#26364D"/>
  <g fill="#26364D"><rect x="42" y="568" width="142" height="4" rx="2"/><rect x="204" y="568" width="142" height="4" rx="2"/><rect x="366" y="568" width="142" height="4" rx="2"/><rect x="528" y="568" width="142" height="4" rx="2"/><rect x="690" y="568" width="142" height="4" rx="2"/><rect x="852" y="568" width="142" height="4" rx="2"/><rect x="1014" y="568" width="144" height="4" rx="2"/></g>

  <g class="scene scene-1" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#2DE2C5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">01 / THESIS</text>
    <text x="42" y="214" fill="#F8FAFC" font-size="54" font-weight="820">INTELLIGENCE IS CHEAP.</text>
    <text x="42" y="274" fill="#2DE2C5" font-size="54" font-weight="820">EVIDENCE IS THE</text>
    <text x="42" y="334" fill="#2DE2C5" font-size="54" font-weight="820">PRODUCT.</text>
    <text x="45" y="386" fill="#AAB8CA" font-size="18">Build long-horizon agents that can show what happened, replay it, and earn trust.</text>
    <g transform="translate(760 160)">
      <circle cx="160" cy="110" r="102" fill="none" stroke="#26364D"/>
      <circle class="pulse" cx="160" cy="110" r="66" fill="none" stroke="#4D7CFE" stroke-width="2"/>
      <circle cx="160" cy="110" r="9" fill="#2DE2C5"/>
      <circle cx="67" cy="54" r="6" fill="#9B7CFF"/><circle cx="248" cy="62" r="6" fill="#F2A93B"/>
      <circle cx="77" cy="181" r="6" fill="#4D7CFE"/><circle cx="242" cy="180" r="6" fill="#2DE2C5"/>
      <path class="trace" d="M67 54 L160 110 L248 62 M77 181 L160 110 L242 180" fill="none" stroke="#65758B" stroke-width="2"/>
      <text x="160" y="254" fill="#91A2B8" font-size="13" text-anchor="middle">CAPABILITY → RECEIPT → TRUST</text>
    </g>
    <rect class="chapter-fill" x="42" y="568" width="142" height="4" rx="2" fill="#2DE2C5"/>
  </g>

  <g class="scene scene-2" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#4D7CFE" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">02 / DIRECT</text>
    <text x="42" y="205" fill="#F8FAFC" font-size="46" font-weight="820">HUMAN AUTHORITY SETS THE CONTRACT.</text>
    <text x="42" y="246" fill="#AAB8CA" font-size="18">Direction is goals, constraints, acceptance criteria, and escalation boundaries.</text>
    <path class="trace" d="M150 356 H1035" fill="none" stroke="#4D7CFE" stroke-width="3"/>
    <g transform="translate(64 309)"><rect width="214" height="96" rx="18" fill="#0B1728" stroke="#4D7CFE"/><text x="107" y="42" fill="#8EB0FF" font-size="13" letter-spacing="1.4" text-anchor="middle">GOALS</text><text x="107" y="68" fill="#F8FAFC" font-size="17" font-weight="700" text-anchor="middle">Define success</text></g>
    <g transform="translate(349 309)"><rect width="214" height="96" rx="18" fill="#0B1728" stroke="#2DE2C5"/><text x="107" y="42" fill="#6EE7D8" font-size="13" letter-spacing="1.4" text-anchor="middle">CONSTRAINTS</text><text x="107" y="68" fill="#F8FAFC" font-size="17" font-weight="700" text-anchor="middle">Bound the system</text></g>
    <g transform="translate(634 309)"><rect width="214" height="96" rx="18" fill="#0B1728" stroke="#9B7CFF"/><text x="107" y="42" fill="#C4B5FD" font-size="13" letter-spacing="1.4" text-anchor="middle">ACCEPTANCE</text><text x="107" y="68" fill="#F8FAFC" font-size="17" font-weight="700" text-anchor="middle">Name the proof</text></g>
    <g transform="translate(919 309)"><rect width="214" height="96" rx="18" fill="#0B1728" stroke="#F2A93B"/><text x="107" y="42" fill="#F8C66E" font-size="13" letter-spacing="1.4" text-anchor="middle">ESCALATION</text><text x="107" y="68" fill="#F8FAFC" font-size="17" font-weight="700" text-anchor="middle">Keep authority</text></g>
    <rect class="chapter-fill" x="204" y="568" width="142" height="4" rx="2" fill="#4D7CFE"/>
  </g>

  <g class="scene scene-3" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#2DE2C5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">03 / ORCHESTRATE</text>
    <text x="42" y="205" fill="#F8FAFC" font-size="46" font-weight="820">TURN CAPABILITY INTO A WORKING SYSTEM.</text>
    <text x="42" y="246" fill="#AAB8CA" font-size="18">Compose specialist agents, models, tools, memory, and loops around one explicit outcome.</text>
    <g transform="translate(452 320)"><circle cx="148" cy="68" r="63" fill="#101D31" stroke="#2DE2C5" stroke-width="2"/><text x="148" y="63" fill="#F8FAFC" font-size="18" font-weight="780" text-anchor="middle">DIRECTED</text><text x="148" y="87" fill="#6EE7D8" font-size="13" text-anchor="middle">SYSTEM</text></g>
    <g fill="#0B1728" stroke="#26364D">
      <rect x="74" y="324" width="210" height="72" rx="16"/><rect x="916" y="324" width="210" height="72" rx="16"/>
      <rect x="211" y="432" width="210" height="72" rx="16"/><rect x="779" y="432" width="210" height="72" rx="16"/>
    </g>
    <g fill="#F8FAFC" font-size="17" font-weight="700" text-anchor="middle"><text x="179" y="368">SPECIALIST AGENTS</text><text x="1021" y="368">MODELS + TOOLS</text><text x="316" y="476">MEMORY</text><text x="884" y="476">FEEDBACK LOOPS</text></g>
    <path class="trace" d="M284 360 H452 M748 360 H916 M421 468 L478 419 M779 468 L722 419" fill="none" stroke="#2DE2C5" stroke-width="2.5"/>
    <rect class="chapter-fill" x="366" y="568" width="142" height="4" rx="2" fill="#2DE2C5"/>
  </g>

  <g class="scene scene-4" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#9B7CFF" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">04 / VERIFY</text>
    <text x="42" y="205" fill="#F8FAFC" font-size="46" font-weight="820">PLAUSIBLE IS NOT THE SAME AS PROVEN.</text>
    <text x="42" y="246" fill="#AAB8CA" font-size="18">Receipts, deterministic replay, and adversarial tests decide what gets accepted.</text>
    <g transform="translate(64 316)" font-family="ui-monospace,SFMono-Regular,monospace">
      <text x="0" y="22" fill="#91A2B8" font-size="12" letter-spacing="1.5">RAW OUTPUT</text>
      <rect x="0" y="48" width="214" height="86" rx="16" fill="#0B1728" stroke="#65758B"/><text x="107" y="100" fill="#F8FAFC" font-size="16" text-anchor="middle">AGENT RESULT</text>
      <path class="trace" d="M214 91 H357" fill="none" stroke="#9B7CFF" stroke-width="3"/>
      <path d="M348 83 L360 91 L348 99" fill="none" stroke="#9B7CFF" stroke-width="3"/>
      <rect x="357" y="22" width="350" height="138" rx="22" fill="#14132A" stroke="#9B7CFF" stroke-width="2"/>
      <text x="532" y="58" fill="#C4B5FD" font-size="12" letter-spacing="1.6" text-anchor="middle">EVIDENCE GATE</text>
      <text x="422" y="106" fill="#F8FAFC" font-size="16">RECEIPT</text><text x="532" y="106" fill="#F8FAFC" font-size="16">REPLAY</text><text x="637" y="106" fill="#F8FAFC" font-size="16">TEST</text>
      <path class="trace" d="M707 91 H850" fill="none" stroke="#2DE2C5" stroke-width="3"/>
      <path d="M841 83 L853 91 L841 99" fill="none" stroke="#2DE2C5" stroke-width="3"/>
      <rect x="850" y="48" width="214" height="86" rx="16" fill="#0B2427" stroke="#2DE2C5"/><text x="957" y="100" fill="#6EE7D8" font-size="16" font-weight="700" text-anchor="middle">ACCEPTED WORK</text>
    </g>
    <text x="600" y="516" fill="#91A2B8" font-size="15" text-anchor="middle">Every important claim should survive independent inspection.</text>
    <rect class="chapter-fill" x="528" y="568" width="142" height="4" rx="2" fill="#9B7CFF"/>
  </g>

  <g class="scene scene-5" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#F2A93B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">05 / IMPROVE</text>
    <text x="42" y="205" fill="#F8FAFC" font-size="46" font-weight="820">MAKE FAILURE STRENGTHEN THE NEXT RUN.</text>
    <text x="42" y="246" fill="#AAB8CA" font-size="18">Measured breakdowns become sharper instructions, tests, and system design.</text>
    <path class="trace" d="M191 390 C235 277 397 273 452 378 C510 490 690 490 748 378 C803 273 965 277 1009 390" fill="none" stroke="#F2A93B" stroke-width="3"/>
    <g text-anchor="middle">
      <circle cx="191" cy="390" r="50" fill="#0B1728" stroke="#4D7CFE"/><text x="191" y="386" fill="#8EB0FF" font-size="12">01</text><text x="191" y="410" fill="#F8FAFC" font-size="15" font-weight="700">DIRECT</text>
      <circle cx="452" cy="378" r="50" fill="#0B1728" stroke="#2DE2C5"/><text x="452" y="374" fill="#6EE7D8" font-size="12">02</text><text x="452" y="398" fill="#F8FAFC" font-size="15" font-weight="700">ORCHESTRATE</text>
      <circle cx="748" cy="378" r="50" fill="#0B1728" stroke="#9B7CFF"/><text x="748" y="374" fill="#C4B5FD" font-size="12">03</text><text x="748" y="398" fill="#F8FAFC" font-size="15" font-weight="700">VERIFY</text>
      <circle cx="1009" cy="390" r="50" fill="#241A0A" stroke="#F2A93B"/><text x="1009" y="386" fill="#F8C66E" font-size="12">04</text><text x="1009" y="410" fill="#F8FAFC" font-size="15" font-weight="700">IMPROVE</text>
    </g>
    <path class="trace" d="M1009 442 C988 525 222 525 191 442" fill="none" stroke="#65758B" stroke-width="2"/>
    <rect class="chapter-fill" x="690" y="568" width="142" height="4" rx="2" fill="#F2A93B"/>
  </g>

  <g class="scene scene-6" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#2DE2C5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">06 / LIVE PROOF</text>
    <text x="42" y="205" fill="#F8FAFC" font-size="46" font-weight="820">THE PROFILE UPDATES AS THE WORK LANDS.</text>
    <text x="42" y="246" fill="#AAB8CA" font-size="18">Public contribution evidence and modeled Agentic Power refresh from source data.</text>
    <g transform="translate(42 318)">
      <line x1="0" y1="0" x2="1116" y2="0" stroke="#26364D"/>
      <line x1="274" y1="0" x2="274" y2="142" stroke="#26364D"/><line x1="558" y1="0" x2="558" y2="142" stroke="#26364D"/><line x1="842" y1="0" x2="842" y2="142" stroke="#26364D"/>
      <text x="0" y="58" fill="#2DE2C5" font-size="42" font-weight="820">{len(repositories)}</text><text x="0" y="88" fill="#91A2B8" font-size="12" letter-spacing="1.3">VERIFIED UPSTREAM PROJECTS</text>
      <text x="308" y="58" fill="#9B7CFF" font-size="42" font-weight="820">{total_merged:,}</text><text x="308" y="88" fill="#91A2B8" font-size="12" letter-spacing="1.3">MERGED PULL REQUESTS</text>
      <text x="592" y="58" fill="#F8FAFC" font-size="42" font-weight="820">{total_line_changes:,}</text><text x="592" y="88" fill="#91A2B8" font-size="12" letter-spacing="1.3">ACCEPTED LINE CHANGES</text>
      <text x="876" y="58" fill="#F2A93B" font-size="42" font-weight="820">{agentic_power:.1f}×</text><text x="876" y="88" fill="#91A2B8" font-size="12" letter-spacing="1.3">PROVISIONAL AGENTIC POWER</text>
      <text x="0" y="132" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11">{len(owned_repositories)} owned public projects · evidence refresh every 30 minutes · assumptions remain visible</text>
    </g>
    <rect class="chapter-fill" x="852" y="568" width="142" height="4" rx="2" fill="#2DE2C5"/>
  </g>

  <g class="scene scene-7" font-family="Avenir Next,Segoe UI,sans-serif">
    <text class="chapter-number" x="42" y="137" fill="#9B7CFF" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">07 / BUILD TOGETHER</text>
    <text x="42" y="218" fill="#F8FAFC" font-size="55" font-weight="820">BUILD AI THAT CAN SURVIVE</text>
    <text x="42" y="284" fill="#9B7CFF" font-size="55" font-weight="820">CONTACT WITH REALITY.</text>
    <text x="45" y="341" fill="#AAB8CA" font-size="19">Agent reliability · evaluation integrity · proof-bound execution · enterprise deployment</text>
    <g transform="translate(45 407)" font-family="ui-monospace,SFMono-Regular,monospace" font-size="13" letter-spacing="1.3">
      <text x="0" fill="#2DE2C5">OPEN SOURCE</text><text x="155" fill="#65758B">•</text><text x="182" fill="#4D7CFE">GRADIA</text><text x="284" fill="#65758B">•</text><text x="311" fill="#9B7CFF">RESEARCH</text><text x="436" fill="#65758B">•</text><text x="463" fill="#F2A93B">COLLABORATE</text>
    </g>
    <rect class="chapter-fill" x="1014" y="568" width="144" height="4" rx="2" fill="#9B7CFF"/>
  </g>

  <g class="scene-static" font-family="Avenir Next,Segoe UI,sans-serif">
    <text x="42" y="137" fill="#2DE2C5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" letter-spacing="2">EVIDENCE-FIRST AGENTIC ENGINEERING</text>
    <text x="42" y="211" fill="#F8FAFC" font-size="45" font-weight="820">DIRECT → ORCHESTRATE → VERIFY → IMPROVE</text>
    <text x="42" y="258" fill="#AAB8CA" font-size="18">Human authority sets the contract. Only accepted, inspectable work counts.</text>
    <g transform="translate(42 329)">
      <text x="0" y="45" fill="#2DE2C5" font-size="38" font-weight="820">{len(repositories)}</text><text x="0" y="72" fill="#91A2B8" font-size="11">VERIFIED PROJECTS</text>
      <text x="270" y="45" fill="#9B7CFF" font-size="38" font-weight="820">{total_merged:,}</text><text x="270" y="72" fill="#91A2B8" font-size="11">MERGED PRS</text>
      <text x="550" y="45" fill="#F8FAFC" font-size="38" font-weight="820">{total_line_changes:,}</text><text x="550" y="72" fill="#91A2B8" font-size="11">ACCEPTED LINE CHANGES</text>
      <text x="870" y="45" fill="#F2A93B" font-size="38" font-weight="820">{agentic_power:.1f}×</text><text x="870" y="72" fill="#91A2B8" font-size="11">PROVISIONAL AP</text>
    </g>
  </g>

  <text x="42" y="598" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5">CONCEPTUAL WALKTHROUGH · LIVE NUMBERS ARE MACHINE-COUNTED · MOTION STOPS WITH REDUCED-MOTION</text>
  <text x="1158" y="598" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5" text-anchor="end">REPLAYING</text>
</svg>
"""


def build_upstream_timeline(
    repositories: list[dict[str, object]],
    updated: str,
    current_open_pull_requests: int | None = None,
) -> dict[str, object]:
    """Build daily cumulative upstream evidence from merged PR receipts."""
    events: list[dict[str, object]] = []
    for repository in repositories:
        full_name = str(repository.get("full_name") or repository.get("name") or "")
        for pull_request in repository.get("_merged_pull_requests", []):
            merged_at = str(
                pull_request.get("mergedAt") or pull_request.get("merged_at") or ""
            )
            if not merged_at:
                continue
            events.append(
                {
                    "day": merged_at[:10],
                    "repository": full_name,
                    "line_changes": int(pull_request.get("additions") or 0)
                    + int(pull_request.get("deletions") or 0),
                }
            )

    end = dt.date.fromisoformat(updated)
    if events:
        first_event = min(dt.date.fromisoformat(str(event["day"])) for event in events)
        start = min(first_event, end) - dt.timedelta(days=1)
    else:
        start = end - dt.timedelta(days=1)
        for repository in repositories:
            merged_prs = int(repository.get("merged_prs") or 0)
            if not merged_prs:
                continue
            events.extend(
                {
                    "day": end.isoformat(),
                    "repository": str(
                        repository.get("full_name") or repository.get("name") or ""
                    ),
                    "line_changes": (
                        int(repository.get("accepted_additions") or 0)
                        + int(repository.get("accepted_deletions") or 0)
                        if index == 0
                        else 0
                    ),
                }
                for index in range(merged_prs)
            )

    events_by_day: dict[str, list[dict[str, object]]] = {}
    for event in events:
        events_by_day.setdefault(str(event["day"]), []).append(event)

    points: list[dict[str, object]] = []
    repositories_seen: set[str] = set()
    merged_total = 0
    line_total = 0
    day = start
    while day <= end:
        day_events = events_by_day.get(day.isoformat(), [])
        new_projects = 0
        for event in day_events:
            repository = str(event["repository"])
            if repository not in repositories_seen:
                repositories_seen.add(repository)
                new_projects += 1
            merged_total += 1
            line_total += int(event["line_changes"])
        points.append(
            {
                "date": day.isoformat(),
                "daily_merged_prs": len(day_events),
                "daily_line_changes": sum(
                    int(event["line_changes"]) for event in day_events
                ),
                "new_projects": new_projects,
                "cumulative_merged_prs": merged_total,
                "cumulative_projects": len(repositories_seen),
                "cumulative_line_changes": line_total,
            }
        )
        day += dt.timedelta(days=1)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "verified_projects": len(repositories),
        "merged_projects": sum(
            1 for repository in repositories if int(repository.get("merged_prs") or 0)
        ),
        "merged_prs": sum(
            int(repository.get("merged_prs") or 0) for repository in repositories
        ),
        "open_prs": (
            int(current_open_pull_requests)
            if current_open_pull_requests is not None
            else sum(
                int(repository.get("open_prs") or 0) for repository in repositories
            )
        ),
        "open_prs_in_verified_projects": sum(
            int(repository.get("open_prs") or 0) for repository in repositories
        ),
        "open_pr_scope_start": PERIOD_START.date().isoformat(),
        "open_pull_requests_url": CURRENT_OPEN_PULL_REQUESTS_URL,
        "accepted_line_changes": sum(
            int(repository.get("accepted_additions") or 0)
            + int(repository.get("accepted_deletions") or 0)
            for repository in repositories
        ),
        "current_repository_reach_stars": sum(
            int(repository.get("stargazers") or 0) for repository in repositories
        ),
        "points": points,
    }


def timeline_path(
    points: list[dict[str, object]],
    key: str,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
) -> str:
    """Map a cumulative evidence series into an SVG polyline path."""
    maximum = max((int(point[key]) for point in points), default=0) or 1
    denominator = max(len(points) - 1, 1)
    commands = []
    for index, point in enumerate(points):
        x = x0 + ((x1 - x0) * index / denominator)
        y = y0 - ((y0 - y1) * int(point[key]) / maximum)
        commands.append(f"{'M' if index == 0 else 'L'} {x:.1f} {y:.1f}")
    return " ".join(commands)


def timeline_frame_values(frame_count: int, active_index: int) -> str:
    """Return a discrete looping opacity sequence for one timeline frame."""
    if frame_count <= 1:
        return "1;1"
    values = ["1" if index == active_index else "0" for index in range(frame_count)]
    values.append("1" if active_index == 0 else "0")
    return ";".join(values)


def timeline_frame_key_times(frame_count: int) -> str:
    """Return evenly spaced key times, including the loop-closing endpoint."""
    frame_count = max(frame_count, 1)
    return ";".join(f"{index / frame_count:.5f}" for index in range(frame_count + 1))


def render_svg(
    repositories: list[dict[str, object]],
    updated: str,
    timeline: dict[str, object] | None = None,
) -> str:
    timeline = timeline or build_upstream_timeline(repositories, updated)
    points = list(timeline["points"])
    x0, x1 = 104.0, 1105.0
    projects_path = timeline_path(
        points, "cumulative_projects", x0, x1, 247.0, 170.0
    )
    merged_path = timeline_path(
        points, "cumulative_merged_prs", x0, x1, 421.0, 292.0
    )
    lines_path = timeline_path(
        points, "cumulative_line_changes", x0, x1, 577.0, 502.0
    )
    denominator = max(len(points) - 1, 1)
    max_daily = max((int(point["daily_merged_prs"]) for point in points), default=0) or 1
    playback_duration = 24
    frame_count = len(points)
    frame_key_times = timeline_frame_key_times(frame_count)
    playback_frames = []
    daily_bars = []
    milestone_dots = []
    for index, point in enumerate(points):
        x = x0 + ((x1 - x0) * index / denominator)
        daily = int(point["daily_merged_prs"])
        if daily:
            height = 8 + (49 * daily / max_daily)
            daily_bars.append(
                f'<rect class="daily-bar" x="{x - 2.2:.1f}" y="{464 - height:.1f}" '
                f'width="4.4" height="{height:.1f}" rx="2.2" '
                f'style="animation-delay:{index * 0.12:.3f}s"/>'
            )
        if int(point["new_projects"]):
            projects_maximum = max(int(item["cumulative_projects"]) for item in points) or 1
            y = 247 - (77 * int(point["cumulative_projects"]) / projects_maximum)
            milestone_dots.append(
                f'<circle class="project-pulse" cx="{x:.1f}" cy="{y:.1f}" r="3.2" '
                f'style="animation-delay:{index * 0.04:.3f}s"/>'
            )
        date_label = dt.date.fromisoformat(str(point["date"])).strftime("%b %d, %Y")
        frame_values = timeline_frame_values(frame_count, index)
        frame_animation = (
            f'<animate attributeName="opacity" dur="{playback_duration}s" '
            f'repeatCount="indefinite" calcMode="discrete" '
            f'keyTimes="{frame_key_times}" values="{frame_values}"/>'
        )
        playback_frames.append(
            f'<g class="playback-frame" opacity="0">{frame_animation}'
            f'<text x="1158" y="92" fill="#2DE2C5" font-size="10.5" text-anchor="end">PLAYBACK · {date_label.upper()}</text>'
            f'<text x="42" y="118" fill="#9B7CFF" font-size="22" font-weight="760">{int(point["cumulative_merged_prs"]):,}</text>'
            f'<text x="459" y="118" fill="#2DE2C5" font-size="22" font-weight="760">{int(point["cumulative_projects"]):,}</text>'
            f'<text x="780" y="118" fill="#F8FAFC" font-size="22" font-weight="760">{compact(int(point["cumulative_line_changes"]))}</text>'
            f'<text x="1124" y="177" fill="#2DE2C5" font-size="26" font-weight="760">{int(point["cumulative_projects"]):,}</text>'
            f'<text x="1124" y="307" fill="#9B7CFF" font-size="26" font-weight="760">{compact(int(point["cumulative_merged_prs"]))}</text>'
            f'<text x="1124" y="509" fill="#4D7CFE" font-size="24" font-weight="760">{compact(int(point["cumulative_line_changes"]))}</text>'
            f'</g>'
        )

    tick_indices = sorted(
        {
            round((len(points) - 1) * fraction / 4)
            for fraction in range(5)
        }
    )
    date_ticks = []
    for index in tick_indices:
        x = x0 + ((x1 - x0) * index / denominator)
        date_label = dt.date.fromisoformat(str(points[index]["date"])).strftime("%b %d")
        date_ticks.append(
            f'<line x1="{x:.1f}" y1="151" x2="{x:.1f}" y2="594" '
            f'stroke="#203149" stroke-dasharray="2 8"/>'
            f'<text x="{x:.1f}" y="618" text-anchor="middle">{date_label}</text>'
        )

    merged_prs = int(timeline["merged_prs"])
    open_prs = int(timeline["open_prs"])
    merged_projects = int(timeline["merged_projects"])
    verified_projects = int(timeline["verified_projects"])
    accepted_lines = int(timeline["accepted_line_changes"])
    stars = int(timeline["current_repository_reach_stars"])
    start = dt.date.fromisoformat(str(timeline["start_date"])).strftime("%b %d, %Y")
    end = dt.date.fromisoformat(str(timeline["end_date"])).strftime("%b %d, %Y")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="690" viewBox="0 0 1200 690" role="img" aria-labelledby="title desc">
  <title id="title">Animated upstream contribution timeline for Rudy Celekli</title>
  <desc id="desc">Daily GitHub evidence from {start} through {end}: {merged_prs:,} merged pull requests across {merged_projects} projects, {open_prs:,} public pull requests authored since 2025 and currently open, {accepted_lines:,} accepted line changes, and {stars:,} current repository stars. Open work is not counted as accepted. Stars describe current repository reach, not personal credit.</desc>
  <defs>
    <style>
      .trace-reveal {{ stroke-dasharray: 1; stroke-dashoffset: 1; animation: reveal 6s cubic-bezier(.16,1,.3,1) forwards; }}
      .trace-reveal.slow {{ animation-delay: .65s; }}
      .daily-bar {{ fill: #4D7CFE; opacity: .72; transform-box: fill-box; transform-origin: center bottom; animation: bar-rise 1.4s cubic-bezier(.16,1,.3,1) both; }}
      .project-pulse {{ fill: #2DE2C5; opacity: .72; animation: point-pulse 3.6s ease-in-out infinite; }}
      .open-orbit {{ transform-box: fill-box; transform-origin: center; animation: orbit 18s linear infinite; }}
      .packet {{ filter: drop-shadow(0 0 7px #9B7CFF); }}
      .playback-frame {{ pointer-events: none; }}
      .playback-static, .playhead-static {{ display: none; }}
      @keyframes reveal {{ to {{ stroke-dashoffset: 0; }} }}
      @keyframes bar-rise {{ from {{ opacity: .3; transform: scaleY(.18); }} to {{ opacity: .72; transform: scaleY(1); }} }}
      @keyframes point-pulse {{ 0%,100% {{ opacity: .52; r: 3.2px; }} 50% {{ opacity: 1; r: 5px; }} }}
      @keyframes orbit {{ to {{ transform: rotate(360deg); }} }}
      @media (prefers-reduced-motion: reduce) {{
        .trace-reveal, .daily-bar, .project-pulse, .open-orbit {{ animation: none !important; }}
        .trace-reveal {{ stroke-dashoffset: 0; }}
        .daily-bar, .project-pulse {{ opacity: .82; }}
        .playback-animated, .packet {{ display: none; }}
        .playback-static, .playhead-static {{ display: block; }}
      }}
    </style>
    <linearGradient id="canvas" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#07111E"/><stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">
      <stop stop-color="#2DE2C5"/><stop offset=".52" stop-color="#4D7CFE"/><stop offset="1" stop-color="#9B7CFF"/>
    </linearGradient>
    <linearGradient id="project-fill" x1="0" y1="0" x2="0" y2="1">
      <stop stop-color="#2DE2C5" stop-opacity=".2"/><stop offset="1" stop-color="#2DE2C5" stop-opacity="0"/>
    </linearGradient>
    <filter id="glow"><feGaussianBlur stdDeviation="4" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
  </defs>
  <rect width="1200" height="690" rx="28" fill="url(#canvas)"/>
  <rect x="1" y="1" width="1198" height="688" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="30" width="76" height="4" rx="2" fill="url(#rule)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="29" font-weight="780">UPSTREAM MOMENTUM</text>
  <text x="42" y="92" fill="#91A2B8" font-family="Avenir Next,Segoe UI,sans-serif" font-size="13">A daily flight recorder of accepted work, expanding project breadth, and the live upstream queue.</text>
  <text x="1158" y="67" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11.5" text-anchor="end">GITHUB · {html.escape(updated)} UTC · AUTO-REFRESH 30 MIN</text>

  <g font-family="ui-monospace,SFMono-Regular,monospace">
    <text x="42" y="135" fill="#91A2B8" font-size="9.5">MERGED PRS</text>
    <text x="268" y="118" fill="#F2A93B" font-size="22" font-weight="760">{open_prs:,}</text><text x="268" y="135" fill="#91A2B8" font-size="9.5">PUBLIC OPEN · CURRENT</text>
    <text x="459" y="135" fill="#91A2B8" font-size="9.5">MERGED PROJECTS · {verified_projects} VERIFIED</text>
    <text x="780" y="135" fill="#91A2B8" font-size="9.5">ACCEPTED LINE CHANGES</text>
    <text x="1046" y="118" fill="#F2A93B" font-size="22" font-weight="760">{compact(stars)}</text><text x="1046" y="135" fill="#91A2B8" font-size="9.5">CURRENT REPO REACH</text>
    <g class="playback-animated">{''.join(playback_frames)}</g>
    <g class="playback-static">
      <text x="1158" y="92" fill="#2DE2C5" font-size="10.5" text-anchor="end">CURRENT · {end.upper()}</text>
      <text x="42" y="118" fill="#9B7CFF" font-size="22" font-weight="760">{merged_prs:,}</text>
      <text x="459" y="118" fill="#2DE2C5" font-size="22" font-weight="760">{merged_projects}</text>
      <text x="780" y="118" fill="#F8FAFC" font-size="22" font-weight="760">{compact(accepted_lines)}</text>
      <text x="1124" y="177" fill="#2DE2C5" font-size="26" font-weight="760">{merged_projects}</text>
      <text x="1124" y="307" fill="#9B7CFF" font-size="26" font-weight="760">{compact(merged_prs)}</text>
      <text x="1124" y="509" fill="#4D7CFE" font-size="24" font-weight="760">{compact(accepted_lines)}</text>
    </g>
  </g>

  <g fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5">
    {''.join(date_ticks)}
  </g>
  <g class="playback-animated" opacity=".58">
    <line x1="104" y1="151" x2="104" y2="594" stroke="#2DE2C5" stroke-width="2">
      <animate attributeName="x1" from="104" to="1105" dur="{playback_duration}s" repeatCount="indefinite"/>
      <animate attributeName="x2" from="104" to="1105" dur="{playback_duration}s" repeatCount="indefinite"/>
    </line>
  </g>
  <line class="playhead-static" x1="1105" y1="151" x2="1105" y2="594" stroke="#2DE2C5" stroke-width="2" opacity=".58"/>

  <g font-family="ui-monospace,SFMono-Regular,monospace">
    <text x="42" y="174" fill="#2DE2C5" font-size="10.5" letter-spacing="1.2">PROJECT BREADTH</text>
    <text x="42" y="193" fill="#91A2B8" font-size="10">FIRST ACCEPTED PR PER UPSTREAM</text>
    <line x1="104" y1="247" x2="1105" y2="247" stroke="#203149"/>
    <path d="{projects_path}" fill="none" stroke="#2DE2C5" stroke-width="2" opacity=".24"/>
    <path class="trace-reveal" d="{projects_path}" fill="none" stroke="#2DE2C5" stroke-width="2.5" pathLength="1"/>
    {''.join(milestone_dots)}
    <text x="1124" y="195" fill="#65758B" font-size="9.5">PROJECTS</text>

    <text x="42" y="307" fill="#9B7CFF" font-size="10.5" letter-spacing="1.2">ACCEPTED PULL REQUESTS</text>
    <text x="42" y="326" fill="#91A2B8" font-size="10">CUMULATIVE MERGED · DAILY PULSE BELOW</text>
    <line x1="104" y1="421" x2="1105" y2="421" stroke="#203149"/>
    <path d="{merged_path}" fill="none" stroke="#9B7CFF" stroke-width="3" opacity=".24"/>
    <path id="merged-path" class="trace-reveal slow" d="{merged_path}" fill="none" stroke="#9B7CFF" stroke-width="3" pathLength="1"/>
    <g>{''.join(daily_bars)}</g>
    <circle class="packet" r="5" fill="#F8FAFC"><animateMotion dur="{playback_duration}s" repeatCount="indefinite"><mpath href="#merged-path"/></animateMotion></circle>
    <text x="1124" y="325" fill="#65758B" font-size="9.5">MERGED</text>

    <text x="42" y="510" fill="#4D7CFE" font-size="10.5" letter-spacing="1.2">ACCEPTED CODE DELTA</text>
    <text x="42" y="529" fill="#91A2B8" font-size="10">ADDITIONS + DELETIONS FROM MERGED PRS</text>
    <line x1="104" y1="577" x2="1105" y2="577" stroke="#203149"/>
    <path d="{lines_path}" fill="none" stroke="#4D7CFE" stroke-width="2.5" opacity=".24"/>
    <path class="trace-reveal slow" d="{lines_path}" fill="none" stroke="#4D7CFE" stroke-width="2.5" pathLength="1"/>
    <text x="1124" y="527" fill="#65758B" font-size="9.5">LINES</text>
  </g>

  <g transform="translate(1022 348)" font-family="ui-monospace,SFMono-Regular,monospace">
    <circle cx="44" cy="42" r="36" fill="#F2A93B" fill-opacity=".05" stroke="#F2A93B" stroke-opacity=".22"/>
    <g class="open-orbit"><circle cx="44" cy="6" r="4" fill="#F2A93B" filter="url(#glow)"/><path d="M44 6 A36 36 0 1 1 43.9 6" fill="none" stroke="#F2A93B" stroke-dasharray="5 9" stroke-opacity=".54"/></g>
    <text x="44" y="40" text-anchor="middle" fill="#F2A93B" font-size="20" font-weight="760">{open_prs:,}</text>
    <text x="44" y="56" text-anchor="middle" fill="#91A2B8" font-size="8.5">OPEN NOW</text>
    <text x="44" y="92" text-anchor="middle" fill="#65758B" font-size="8.5">NOT ACCEPTED</text>
  </g>

  <line x1="42" y1="640" x2="1158" y2="640" stroke="#203149"/>
  <text x="42" y="663" fill="#2DE2C5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5">DETAILS + PROOF LINKS IN THE SORTABLE TABLE BELOW</text>
  <text x="1158" y="663" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5" text-anchor="end">OPEN ≠ MERGED · STARS = CURRENT REPOSITORY REACH</text>
</svg>
"""


def render_impact_signature_svg(
    analysis: dict[str, object], updated: str
) -> str:
    """Render a compact analytical view of accepted upstream change patterns."""
    categories = list(analysis.get("categories") or [])
    patterns = list(analysis.get("patterns") or [])
    total = int(analysis.get("classified_merged_prs") or 0)
    colors = {
        "Reliability & correctness": "#2DE2C5",
        "Testing & evaluation": "#9B7CFF",
        "Maintenance & integration": "#65758B",
        "Performance & scale": "#4D7CFE",
        "Security & trust": "#F2A93B",
        "Product capability": "#F8FAFC",
        "Developer experience": "#AAB8CA",
        "Agent infrastructure": "#6EE7D8",
    }
    spectrum_x = 42.0
    spectrum_width = 1116.0
    spectrum_segments = []
    cursor = spectrum_x
    for category in categories:
        count = int(category["merged_prs"])
        width = spectrum_width * count / max(total, 1)
        color = colors.get(str(category["category"]), "#91A2B8")
        spectrum_segments.append(
            f'<rect x="{cursor:.1f}" y="123" width="{width:.1f}" height="13" '
            f'fill="{color}" opacity=".9"/>'
        )
        cursor += width

    maximum = max((int(item["merged_prs"]) for item in categories), default=1)
    category_rows = []
    for index, category in enumerate(categories):
        y = 184 + index * 49
        count = int(category["merged_prs"])
        count_label = f"{count:,}" if count < 100_000 else compact(count)
        share = float(category["share_pct"])
        projects = int(category["projects_touched"])
        # Keep a permanent gutter between the bar and the count column.
        # A full-width bar must never camouflage same-color digits.
        width = 396 * count / maximum
        color = colors.get(str(category["category"]), "#91A2B8")
        category_rows.append(
            f'<text x="42" y="{y}" fill="#F8FAFC" font-size="12.5" font-weight="650">{html.escape(str(category["category"]))}</text>'
            f'<text x="42" y="{y + 16}" fill="#65758B" font-size="9.5">{projects} PROJECTS TOUCHED</text>'
            f'<rect x="241" y="{y - 11}" width="396" height="8" rx="4" fill="#18263A"/>'
            f'<rect class="impact-bar" x="241" y="{y - 11}" width="{width:.1f}" height="8" rx="4" fill="{color}" style="animation-delay:{index * 0.07:.2f}s"/>'
            f'<text x="716" y="{y}" fill="{color}" font-size="12.5" font-weight="760" text-anchor="end">{count_label}</text>'
            f'<text x="798" y="{y}" fill="#91A2B8" font-size="11" text-anchor="end">{share:.1f}%</text>'
        )

    pattern_groups = []
    for index, pattern in enumerate(patterns[:3]):
        y = 193 + index * 122
        value_lines = wrap_svg_text(pattern["value"], width=37, lines=2)
        detail_lines = wrap_svg_text(pattern["detail"], width=43, lines=2)
        value_svg = "".join(
            f'<text x="840" y="{y + 25 + line_index * 21}" fill="#F8FAFC" font-size="16" font-weight="720">{html.escape(line)}</text>'
            for line_index, line in enumerate(value_lines)
        )
        detail_start = y + 70 if len(value_lines) > 1 else y + 49
        detail_svg = "".join(
            f'<text x="840" y="{detail_start + line_index * 16}" fill="#91A2B8" font-size="10.5">{html.escape(line)}</text>'
            for line_index, line in enumerate(detail_lines)
        )
        pattern_groups.append(
            f'<g class="pattern-readout" style="animation-delay:{0.25 + index * 0.15:.2f}s">'
            f'<text x="840" y="{y}" fill="#2DE2C5" font-size="9.5" letter-spacing="1.2">{html.escape(str(pattern["label"]))}</text>'
            f'{value_svg}{detail_svg}</g>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="610" viewBox="0 0 1200 610" role="img" aria-labelledby="impact-title impact-desc">
  <title id="impact-title">Accepted upstream impact signature for Rudy Celekli</title>
  <desc id="impact-desc">Distribution and cross-project patterns for {total:,} merged pull requests, classified by deterministic title signals. The categories describe dominant intent and do not replace code-level review.</desc>
  <defs>
    <style>
      .impact-bar, .pattern-readout {{ opacity: 1; }}
    </style>
    <linearGradient id="impact-canvas" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#07111E"/><stop offset="1" stop-color="#150B2E"/></linearGradient>
    <linearGradient id="impact-rule" x1="0" y1="0" x2="1" y2="0"><stop stop-color="#2DE2C5"/><stop offset=".52" stop-color="#4D7CFE"/><stop offset="1" stop-color="#9B7CFF"/></linearGradient>
  </defs>
  <rect width="1200" height="610" rx="28" fill="url(#impact-canvas)"/>
  <rect x="1" y="1" width="1198" height="608" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="30" width="76" height="4" rx="2" fill="url(#impact-rule)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="29" font-weight="780">IMPACT SIGNATURE</text>
  <text x="42" y="92" fill="#91A2B8" font-family="Avenir Next,Segoe UI,sans-serif" font-size="13">What accepted upstream work changes, classified from merged PR title evidence.</text>
  <text x="1158" y="67" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11.5" text-anchor="end">{total:,} CLASSIFIED MERGES · {html.escape(updated)} UTC</text>
  <g>{''.join(spectrum_segments)}</g>
  <text x="42" y="153" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5">EXCLUSIVE DOMINANT-INTENT SHARE · SEGMENTS SUM TO 100%</text>
  <g fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="8.5" letter-spacing=".8">
    <text x="716" y="158" text-anchor="end">MERGED PRS</text>
    <text x="798" y="158" text-anchor="end">SHARE</text>
  </g>
  <line x1="661" y1="169" x2="661" y2="548" stroke="#203149"/>
  <line x1="818" y1="169" x2="818" y2="548" stroke="#203149"/>
  <g font-family="ui-monospace,SFMono-Regular,monospace">{''.join(category_rows)}</g>
  <g font-family="ui-monospace,SFMono-Regular,monospace">{''.join(pattern_groups)}</g>
  <line x1="42" y1="566" x2="1158" y2="566" stroke="#203149"/>
  <text x="42" y="588" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5">TITLE-SIGNAL ANALYSIS · ONE PRIMARY CATEGORY PER MERGED PR · FULL BREAKDOWN IN DATA/CONTRIBUTIONS.JSON</text>
  <text x="1158" y="588" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="9.5" text-anchor="end">REFRESHES EVERY 30 MIN</text>
</svg>
"""


def render_owned_project_card(repo: dict[str, object], index: int) -> str:
    """Render one compact, overflow-safe owner-project component."""
    column = index % 3
    row = index // 3
    x = 42 + column * 372
    y = 122 + row * 174
    description_lines = wrap_svg_text(repo["description"], width=43, lines=2)
    description = "".join(
        f'<text x="24" y="{76 + line_index * 17}" fill="#91A2B8" font-size="11.5">{html.escape(line)}</text>'
        for line_index, line in enumerate(description_lines)
    )
    pushed = str(repo.get("pushed_at") or "")[:10] or "unknown"
    return f"""
    <g transform="translate({x} {y})">
      <defs><clipPath id="owned-card-{index}"><rect width="354" height="156" rx="18"/></clipPath></defs>
      <rect width="354" height="156" rx="18" fill="#0B1728" stroke="{repo['accent']}" stroke-opacity="0.38"/>
      <g clip-path="url(#owned-card-{index})">
        <rect class="owned-signal" x="24" y="21" width="42" height="3" rx="1.5" fill="{repo['accent']}"/>
        <text x="24" y="51" fill="#F8FAFC" font-size="19" font-weight="760">{html.escape(ellipsize(repo['name'], 20))}</text>
        <text x="330" y="49" fill="{repo['accent']}" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10" text-anchor="end">OWNER</text>
        {description}
        <line x1="24" y1="115" x2="330" y2="115" stroke="#203149"/>
        <text x="24" y="140" fill="#F8FAFC" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10.5">{compact_counted(int(repo['contributor_commits']), 'COMMIT')} · {compact_counted(int(repo['stargazers']), 'STAR')} · {compact_counted(int(repo['forks']), 'FORK')}</text>
        <text x="330" y="140" fill="#65758B" font-family="ui-monospace,SFMono-Regular,monospace" font-size="10" text-anchor="end">{html.escape(pushed)}</text>
      </g>
    </g>"""


def render_owned_svg(
    projects: list[dict[str, object]], total_count: int, updated: str
) -> str:
    """Render the ranked owner-project spotlight as a separate visual."""
    cards = [render_owned_project_card(repo, index) for index, repo in enumerate(projects)]
    rows = max(math.ceil(len(projects) / 3), 1)
    height = 122 + rows * 156 + max(rows - 1, 0) * 18 + 66
    footer_y = height - 28
    project_names = ", ".join(str(repo["name"]) for repo in projects)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
  <title id="title">Public projects owned by Rudy Celekli</title>
  <desc id="desc">Automatically discovered public owner repositories, spotlighting {html.escape(project_names)}. The complete qualifying set remains in machine-readable evidence.</desc>
  <defs>
    <style>
      .owned-signal {{ animation: owner-pulse 4.2s ease-in-out infinite; }}
      @keyframes owner-pulse {{ 0%, 100% {{ opacity: .52; }} 50% {{ opacity: 1; }} }}
      @media (prefers-reduced-motion: reduce) {{ .owned-signal {{ animation: none; opacity: 1; }} }}
    </style>
    <linearGradient id="owned-canvas" x1="0" y1="0" x2="1" y2="1">
      <stop stop-color="#07111E"/>
      <stop offset="1" stop-color="#150B2E"/>
    </linearGradient>
    <linearGradient id="owned-rule" x1="0" y1="0" x2="1" y2="0">
      <stop stop-color="#4D7CFE"/>
      <stop offset="0.5" stop-color="#2DE2C5"/>
      <stop offset="1" stop-color="#9B7CFF"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="{height}" rx="28" fill="url(#owned-canvas)"/>
  <rect x="1" y="1" width="1198" height="{height - 2}" rx="27" fill="none" stroke="#26364D"/>
  <rect x="42" y="30" width="66" height="4" rx="2" fill="url(#owned-rule)"/>
  <text x="42" y="67" fill="#F8FAFC" font-family="Avenir Next,Segoe UI,sans-serif" font-size="27" font-weight="780">PUBLIC PROJECTS, OWNED</text>
  <text x="1158" y="66" fill="#91A2B8" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" text-anchor="end">AUTO-DISCOVERED · {html.escape(updated)} UTC</text>
  <text x="42" y="94" fill="#91A2B8" font-family="Avenir Next,Segoe UI,sans-serif" font-size="13">Ownership is explicit. Activity and reach are repository context, not upstream contribution credit.</text>
  <g font-family="Avenir Next,Segoe UI,sans-serif">{''.join(cards)}</g>
  <g transform="translate(42 {footer_y})" font-family="ui-monospace,SFMono-Regular,monospace" font-size="11.5">
    <text fill="#2DE2C5">{total_count} qualifying owned repos</text>
    <text x="185" fill="#65758B">•</text>
    <text x="205" fill="#9B7CFF">{len(projects)} spotlighted</text>
    <text x="1116" fill="#65758B" text-anchor="end">public · source · non-fork · active</text>
  </g>
</svg>
"""


def render_readme_section(
    repositories: list[dict[str, object]],
    owned_repositories: list[dict[str, object]],
    updated: str,
) -> str:
    preview = repositories[:README_EVIDENCE_PREVIEW_LIMIT]
    lines = [
        "<!-- contribution-stats:start -->",
        "## Open-source impact, verified",
        "",
        f"Every 30 minutes, the ledger discovers public upstream attribution and rebuilds this timeline from individual GitHub receipts. Only merged PRs contribute accepted-code totals. The amber orbit is the [current public open queue]({CURRENT_OPEN_PULL_REQUESTS_URL}), never accepted work. Stars and forks describe repository reach, not personal credit.",
        "",
        f'<img src="./assets/open-source-contributions.svg" width="100%" alt="Animated GitHub upstream timeline showing merged pull requests, open pull requests, unique projects, accepted line changes, and current repository reach through {html.escape(updated)}" />',
        "",
        f'<img src="./assets/impact-signature.svg" width="100%" alt="Impact-category distribution and cross-project patterns across accepted upstream pull requests through {html.escape(updated)}" />',
        "",
        "### Selected evidence",
        "",
        f"The ten highest-signal rows are shown here. **[Open all {len(repositories)} verified projects in the sortable evidence explorer →](https://rudycelekli.github.io/contributions.html)**",
        "",
        "| Project, purpose and evidence | Primary impact | GitHub API rank | Attributed commits | Merged PRs | Accepted lines (+ / −) | Files | Repository reach |",
        "|:--|:--|--:|--:|--:|--:|--:|--:|",
    ]
    for repo in preview:
        tier = str(repo.get("verification_tier") or "github_listed_contributor")
        evidence_url = (
            repo["contributors_url"]
            if tier == "github_listed_contributor"
            else repo["commits_url"]
            if tier == "default_branch_commit_verified"
            else repo.get("contribution_graph_evidence_url") or repo["url"]
            if tier == "github_contribution_graph_verified"
            else repo["pull_requests_url"]
        )
        evidence_label = str(
            repo.get("verification_label") or "GitHub-listed contributor"
        )
        merged_prs = int(repo["merged_prs"])
        attributed_commits = int(repo["contributor_commits"])
        contributor_rank = repo.get("contributor_rank")
        rank_cell = (
            f"[#{int(contributor_rank):,}]({repo['contributors_url']})"
            if contributor_rank is not None
            else "—"
        )
        accepted_lines = (
            f"+{int(repo['accepted_additions']):,} / "
            f"−{int(repo['accepted_deletions']):,}"
            if merged_prs
            else "—"
        )
        accepted_files = (
            f"{int(repo['accepted_changed_files']):,}" if merged_prs else "—"
        )
        description = markdown_table_description(repo.get("description"))
        impact_category = str(
            repo.get("impact_category") or "Engineering contribution"
        )
        impact_category_prs = int(repo.get("impact_category_prs") or 0)
        impact_basis = (
            f"{impact_category_prs:,} of {merged_prs:,} merged PRs"
            if merged_prs
            else "GitHub commit attribution"
        )
        lines.append(
            f"| **[{repo['name']}]({repo['url']})** · "
            f"[{evidence_label}]({evidence_url})<br><sub>{description}</sub> "
            f"| **{impact_category}**<br><sub>{impact_basis}</sub> "
            f"| {rank_cell} "
            f"| {f'{attributed_commits:,}' if attributed_commits else '—'} "
            f"| [{merged_prs:,}]({repo['pull_requests_url']}) "
            f"| {accepted_lines} "
            f"| {accepted_files} "
            f"| {int(repo['stargazers']):,} ★ · {int(repo['forks']):,} forks |"
        )
    lines.extend(
        [
            "",
            f"<sub>Showing {len(preview)} of {len(repositories)} verified upstream projects · {len(owned_repositories)} qualifying owned public repositories tracked separately · [sortable explorer](https://rudycelekli.github.io/contributions.html) · [machine-readable evidence](./data/contributions.json)</sub>",
            "",
            "<sub>Impact categories are deterministic dominant-intent signals from merged PR titles, not a substitute for code review. GitHub contributor rank is the API's one-based all-time ordering; unavailable rank is not treated as zero.</sub>",
        ]
    )
    lines.extend(
        [
            f"<sub>Last verified {updated} UTC · visual + evidence refreshed every 30 minutes by [GitHub Actions](./.github/workflows/refresh-contribution-stats.yml) · [machine-readable evidence](./data/contributions.json)</sub>",
            "<!-- contribution-stats:end -->",
        ]
    )
    return "\n".join(lines)


def render_agentic_power_readme(profile: dict[str, object]) -> str:
    power = profile["agentic_power_x"]
    heh = profile["skilled_human_equivalent_hours"]
    direction = profile["modeled_human_direction_hours"]
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
            "| Measurement | Current evidence | Confidence boundary |",
            "|:--|:--|:--|",
            f"| **Provisional AP** | **{float(power['base']):.1f}×**, with a {float(power['low']):.1f}×–{float(power['high']):.1f}× scenario range | Derived estimate, not a time-tracked audit |",
            f"| **Accepted-work numerator** | {float(heh['base']):,.1f} skilled Human-Equivalent Hours ({weeks:.1f} engineer-weeks) | Model-based task equivalence from accepted evidence |",
            f"| **Direction denominator** | {float(direction['base']):,.1f} hours, midpoint of {float(direction['low']):,.1f}–{float(direction['high']):,.1f} | Operator recall: {float(calibration['operator_estimated_hours_per_week']['low']):.1f}–{float(calibration['operator_estimated_hours_per_week']['high']):.1f} active hours per week |",
            f"| **Evidence coverage** | {public_items:,} merged upstream PRs plus {repository_commits:,} authored default-branch commits across {repository_count} accessible repositories | Private work remains redacted; only accessible repositories are counted |",
            "",
            "Direction includes active briefing, steering, review, correction, and coordination. It excludes agent runtime and waiting. This is a transparent scenario with inspectable inputs, not a completed Full Evidence Audit.",
            "",
            "<sub>[Framework and formula](https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/) · [calculation evidence](./data/agentic-power.json) · public upstream evidence and visuals refreshed every 30 minutes; redacted repository snapshot retained until a private read credential is available</sub>",
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
    current_open_pull_requests = discover_current_open_pull_request_count(token)
    owned_repositories = discover_owned_repositories(token)
    if not owned_repositories:
        raise RuntimeError("No qualifying owned public repositories were discovered")
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
    merged_pull_request_activity = public_merged_pull_request_activity(repositories)
    upstream_timeline = build_upstream_timeline(
        repositories, updated, current_open_pull_requests
    )
    impact_analysis = build_impact_analysis(repositories)
    payload = {
        "schema_version": 9,
        "source": "GitHub GraphQL and REST APIs",
        "login": LOGIN,
        "updated_at_utc": updated,
        "methodology": {
            "project_discovery": "Every public, non-fork repository outside the login's account found through exhaustive authored-PR history or year-windowed GitHub contribution-graph commit attribution is examined automatically on each run.",
            "project_inclusion": "A project is included when GitHub verifies at least one attribution signal: Contributors API listing, contribution-graph commit credit, an authored commit present on the default branch, or an authored merged pull request on any branch.",
            "verification_tiers": "Evidence upgrades automatically from merged-PR verified or GitHub-graph verified, to default-branch commit verified, to GitHub-listed contributor as stronger GitHub attribution becomes available.",
            "contributor_commits": "The GitHub Contributors API count is used at the highest tier. Before it catches up, directly attributed default-branch commits or GitHub contribution-graph commits are reported with their distinct evidence tier. GitHub indexes can disagree temporarily and may lag by several hours.",
            "contributor_rank": "All-time contributor rank is the login's one-based position in GitHub's Contributors API ordering. The top-profile proof includes only observed ranks 1 through 5 and omits unavailable or pending indexes.",
            "accepted_code": "Additions, deletions, and changed files from merged pull requests only.",
            "open_pull_requests": "Every public pull request authored since 2025 whose current GitHub state is OPEN is reported as a point-in-time queue, including drafts and repositories that have not entered the verified contribution ledger. Open work is never counted as accepted work.",
            "merged_pull_request_activity": "Every public authored merged pull request discovered by the exhaustive date-partitioned GitHub search is retained as a distinct receipt with repository, URL, merge time, code-change statistics, reviews, and comments.",
            "upstream_timeline": "Daily cumulative project breadth, merged pull requests, and accepted line changes are rebuilt from individual merged-PR receipts. Repository stars are shown only as current point-in-time reach because historical star observations are not available.",
            "repository_reach": "Stars and forks are current repository-level context, not personal contribution credit or historical growth.",
            "impact_categories": "Each merged pull request is assigned one deterministic impact category from its title: security and trust, testing and evaluation, performance and scale, reliability and correctness, agent infrastructure, developer experience, product capability, or maintenance and integration. The table shows the dominant category and the evidence file retains the complete per-project breakdown.",
            "owned_project_discovery": "Every public, non-fork, non-archived, active source repository owned by the login is discovered automatically on each run, except the profile repository and explicit authorship exclusions.",
            "owned_project_ranking": "The profile spotlight ranks the complete discovered set by stars, recent activity, then GitHub-attributed commits. Every qualifying owned repository remains in this evidence file.",
            "release_discovery": "The newest public, non-draft GitHub release or prerelease is discovered for every owned public project. A recent release adds a decaying 150-day launch signal and is linked directly in the profile.",
            "building_now_ranking": "The top-fold public frontier is re-ranked on every run using repository reach, 120-day shipping recency, recent public releases, attributed commits, and project completeness signals. Gradia remains a separately disclosed AI Research and Implementation Lab anchor; its verified-software-factory direction is labeled in development and contributes no private repository details.",
            "ownership_boundary": "Repository ownership is reported separately and is never counted as upstream contributor credit or accepted merged-PR work.",
        },
        "repositories": public_repositories,
        "merged_pull_requests": merged_pull_request_activity,
        "upstream_timeline": upstream_timeline,
        "impact_analysis": impact_analysis,
        "owned_repositories": owned_repositories,
        "owned_project_spotlight_limit": OWNED_PROJECT_DISPLAY_LIMIT,
        "owned_project_exclusions": sorted(OWNED_PROJECT_EXCLUSIONS),
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_generated(DATA_FILE, json.dumps(payload, indent=2, sort_keys=True))
    write_generated(
        AP_DATA_FILE, json.dumps(agentic_power, indent=2, sort_keys=True)
    )
    write_generated(SVG_FILE, render_svg(repositories, updated, upstream_timeline))
    write_generated(
        IMPACT_SVG_FILE, render_impact_signature_svg(impact_analysis, updated)
    )
    write_generated(
        OWNED_SVG_FILE,
        render_owned_svg(
            owned_repositories[:OWNED_PROJECT_DISPLAY_LIMIT],
            len(owned_repositories),
            updated,
        ),
    )
    write_generated(AP_SVG_FILE, render_agentic_power_svg(agentic_power))
    write_generated(HERO_SVG_FILE, render_hero_svg(repositories, agentic_power))
    write_generated(
        AUTHORITY_SVG_FILE,
        render_contributor_authority_svg(repositories, updated),
    )
    write_generated(
        LATEST_WORK_SVG_FILE,
        render_latest_verified_work_svg(
            merged_pull_request_activity,
            owned_repositories,
            repositories,
            updated,
        ),
    )
    write_generated(
        BUILDING_SVG_FILE,
        render_building_now_svg(owned_repositories, updated),
    )
    write_generated(
        WALKTHROUGH_SVG_FILE,
        render_profile_walkthrough_svg(
            repositories, owned_repositories, agentic_power
        ),
    )
    update_readme(
        "agentic-power-profile", render_agentic_power_readme(agentic_power)
    )
    update_readme(
        "building-now", render_building_now_readme(owned_repositories, updated)
    )
    update_readme(
        "top-contributor-proof",
        render_top_contributor_readme(repositories, updated),
    )
    update_readme(
        "contribution-stats",
        render_readme_section(repositories, owned_repositories, updated),
    )
    print(
        "Updated verified upstream contribution evidence: "
        + ", ".join(
            f"{repo['name']}={repo['contributor_commits']} commits/{repo['merged_prs']} merged PRs"
            for repo in repositories
        )
    )
    print(
        "Updated owned public project evidence: "
        f"{len(owned_repositories)} discovered, "
        f"{min(len(owned_repositories), OWNED_PROJECT_DISPLAY_LIMIT)} spotlighted"
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
