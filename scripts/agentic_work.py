"""Dated work evidence for a rolling twelve-month Agentic Power scenario.

Public PR timelines are public receipts. Owned-repository records publish only a
one-way commit digest, month, visibility, and modeled outcome class, never private
repository names, raw SHAs, exact timestamps, or commit text.
"""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import math
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP

UTC = dt.timezone.utc
DIRECTION_HOURS_PER_DAY = 2.0
MODEL_VERSION = "rolling-12-months-authored-work-v3"


def timestamp(value):
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps must include a timezone")
    return parsed.astimezone(UTC)


def month_windows(updated):
    end_date = dt.date.fromisoformat(updated)
    index = end_date.year * 12 + end_date.month - 1
    months = []
    for offset in range(11, -1, -1):
        year, month0 = divmod(index - offset, 12)
        month = month0 + 1
        days = end_date.day if offset == 0 else calendar.monthrange(year, month)[1]
        months.append(
            {
                "month": f"{year:04d}-{month:02d}",
                "elapsed_days": days,
                "direction_hours": days * DIRECTION_HOURS_PER_DAY,
                "partial_month": offset == 0
                and days < calendar.monthrange(year, month)[1],
            }
        )
    return months


def digest(sha):
    return hashlib.sha256(sha.encode("ascii")).hexdigest()


def allocate_hours(hours, month_counts):
    """Allocate integer cents once, conserving each PR's full modeled HEH."""
    cents = int(
        (Decimal(str(hours)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )
    if (
        cents < 0
        or not month_counts
        or any(count <= 0 for count in month_counts.values())
    ):
        raise ValueError("Invalid work allocation")
    total = sum(month_counts.values())
    result = {month: cents * count // total for month, count in month_counts.items()}
    residual = cents - sum(result.values())
    order = sorted(
        month_counts, key=lambda month: (-(cents * month_counts[month] % total), month)
    )
    for month in order[:residual]:
        result[month] += 1
    return {month: value / 100 for month, value in sorted(result.items())}


def collect_pr_timelines(
    token, repositories, cached, graphql, checkpoint=None, rest=None
):
    """Collect complete public PR commit pages; reuse immutable accepted heads."""
    approved = {
        pr["url"]
        for repo in repositories
        for pr in repo.get("_merged_pull_requests", [])
    }
    entries = {
        url: entry
        for url, entry in (cached or {}).get("pull_requests", {}).items()
        if url in approved
    }
    pending = []
    for repo in repositories:
        for pr in repo.get("_merged_pull_requests", []):
            key = pr["url"]
            expected = int(pr["commits"]["totalCount"])
            old = entries.get(key)
            if (
                not old
                or len(old["commits"]) != expected
                or old.get("commit_count") != expected
            ):
                pending.append((repo["full_name"], pr["number"], key))
    for offset in range(0, len(pending), 25):
        batch = pending[offset : offset + 25]
        connections = []
        for index, (full_name, number, _key) in enumerate(batch):
            owner, name = full_name.split("/", 1)
            connections.append(
                f"r{index}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) {{ isPrivate pullRequest(number:{int(number)}) {{ commits(first:100) {{ totalCount pageInfo {{ hasNextPage endCursor }} nodes {{ commit {{ oid authoredDate author {{ user {{ login }} }} parents {{ totalCount }} }} }} }} }} }}"
            )
        data = graphql(token, "query { " + " ".join(connections) + " }", {})
        for index, (full_name, number, key) in enumerate(batch):
            if data[f"r{index}"].get("isPrivate", False):
                raise RuntimeError(
                    "A private repository cannot enter the public PR timeline cache"
                )
            connection = data[f"r{index}"]["pullRequest"]["commits"]
            nodes = list(connection["nodes"])
            total = int(connection["totalCount"])
            while connection["pageInfo"]["hasNextPage"]:
                owner, name = full_name.split("/", 1)
                query = """query($owner:String!, $name:String!, $number:Int!, $cursor:String!) {
                  repository(owner:$owner,name:$name) { pullRequest(number:$number) {
                    commits(first:100,after:$cursor) { totalCount pageInfo { hasNextPage endCursor }
                      nodes { commit { oid authoredDate author { user { login } } parents { totalCount } } }
                    }
                  } }
                }"""
                connection = graphql(
                    token,
                    query,
                    {
                        "owner": owner,
                        "name": name,
                        "number": number,
                        "cursor": connection["pageInfo"]["endCursor"],
                    },
                )["repository"]["pullRequest"]["commits"]
                nodes.extend(connection["nodes"])
            if len(nodes) == 250 and total > 250 and rest:
                # GitHub caps the PR connection at 250 entries. The paginated
                # comparison endpoint supports the complete base-to-head range.
                metadata = rest(
                    token, f"https://api.github.com/repos/{full_name}/pulls/{number}"
                )
                base, head = metadata["base"]["sha"], metadata["head"]["sha"]
                compared = []
                for page in range(1, math.ceil(total / 100) + 1):
                    result = rest(
                        token,
                        f"https://api.github.com/repos/{full_name}/compare/{base}...{head}?per_page=100&page={page}",
                    )
                    if int(result["total_commits"]) != total:
                        raise RuntimeError(
                            "PR comparison does not match accepted commit count"
                        )
                    compared.extend(result["commits"])
                nodes = [
                    {
                        "commit": {
                            "oid": row["sha"],
                            "authoredDate": row["commit"]["author"]["date"],
                            "author": {"user": row.get("author")},
                            "parents": {"totalCount": len(row["parents"])},
                        }
                    }
                    for row in compared
                ]
            if (
                len(nodes) != total
                or len({node["commit"]["oid"] for node in nodes}) != total
            ):
                raise RuntimeError(
                    f"Incomplete or duplicate public PR timeline: {key}, rows={len(nodes)}, unique={len({node['commit']['oid'] for node in nodes})}, expected={total}"
                )
            commits = []
            for node in nodes:
                commit = node["commit"]
                authored_at = timestamp(commit["authoredDate"]).isoformat()
                user = (commit.get("author") or {}).get("user") or {}
                commits.append(
                    {
                        "sha": commit["oid"],
                        "authored_at": authored_at,
                        "author_login": user.get("login"),
                        "parent_count": int(commit["parents"]["totalCount"]),
                    }
                )
            entries[key] = {"commit_count": total, "commits": commits}
        if checkpoint:
            checkpoint(
                {
                    "schema_version": 1,
                    "source": "GitHub public PR commit connection, all pages",
                    "pull_requests": entries,
                }
            )
        print(
            f"Public PR commit timelines: {min(offset + 25, len(pending))}/{len(pending)} collected",
            flush=True,
        )
    return {
        "schema_version": 1,
        "source": "GitHub public PR commit connection, all pages",
        "pull_requests": entries,
    }


def commit_class(title):
    match = re.match(r"([A-Za-z0-9_-]+)(?:\([^)]*\))?[!:]", title)
    prefix = match.group(1).lower() if match else "other"
    for category, prefixes, hours in (
        (
            "feature_and_research",
            {
                "feat",
                "release",
                "research",
                "console",
                "api",
                "site",
                "about",
                "welcome",
            },
            2.5,
        ),
        ("fix_and_refactor", {"fix", "refactor", "security"}, 1.5),
        ("quality", {"test", "tests", "ci", "build"}, 1.0),
        ("knowledge", {"docs", "paper", "data"}, 0.75),
    ):
        if prefix in prefixes:
            return category, hours
    return "maintenance", 0.5


def collect_owned_work(
    updated, login, search, cached=None, include_private=False, exclusions=()
):
    """Rebuild accessible owned default-branch evidence; retain dated private cache."""
    windows = month_windows(updated)
    start = dt.date.fromisoformat(windows[0]["month"] + "-01")
    end = dt.date.fromisoformat(updated)
    receipts = {}
    private_through = None
    private_from = None
    private_count = 0
    if cached and not include_private:
        private_through = cached.get("private_through_utc")
        private_from = cached.get("private_from_utc")
        private_count = int(cached.get("private_repository_count", 0))
        receipts = {
            key: row
            for key, row in cached.get("receipts", {}).items()
            if row["visibility"] == "private"
            and windows[0]["month"] <= row["month"] <= windows[-1]["month"]
        }
    seen = set()
    public_repos, private_repos = set(), set()
    for window in windows:
        first = dt.date.fromisoformat(window["month"] + "-01")
        last = min(
            end, first.replace(day=calendar.monthrange(first.year, first.month)[1])
        )
        for commit in search(first, last):
            repo = commit["repository"]
            full_name = str(repo["full_name"])
            if (
                full_name.split("/", 1)[0].casefold() != login.casefold()
                or repo.get("fork")
                or full_name.casefold() in exclusions
            ):
                continue
            private = bool(repo.get("private"))
            if private and not include_private:
                continue
            if len(commit.get("parents", [])) > 1:
                continue
            authored = timestamp(commit["commit"]["author"]["date"])
            if not start <= authored.date() <= end:
                continue
            sha = str(commit["sha"])
            key = digest(sha)
            # Prefer a public copy over a private copy of identical work.
            if sha in seen and (private or receipts[key]["visibility"] == "public"):
                continue
            seen.add(sha)
            title = str(commit["commit"]["message"]).splitlines()[0]
            category, base = commit_class(title)
            receipts[key] = {
                "month": authored.strftime("%Y-%m"),
                "visibility": "private" if private else "public",
                "category": category,
                "heh": {
                    "low": round(base * 0.7, 2),
                    "base": base,
                    "high": round(base * 1.4, 2),
                },
            }
            (private_repos if private else public_repos).add(full_name)
        print(
            f"Owned-repository evidence: {window['month']} scanned (private identities redacted)",
            flush=True,
        )
    if include_private:
        private_through, private_count = updated, len(private_repos)
        private_from = start.isoformat()
    return {
        "schema_version": 1,
        "scope": "Accessible personally owned non-fork repositories; profile and authorship exceptions excluded",
        "public_through_utc": updated,
        "public_from_utc": start.isoformat(),
        "private_through_utc": private_through,
        "private_from_utc": private_from,
        "public_repository_count": len(public_repos),
        "private_repository_count": private_count,
        "privacy": "Only SHA-256 commit digests, month, visibility and modeled category/HEH are published; no private names, raw SHAs, exact dates, messages, URLs or code.",
        "receipts": dict(sorted(receipts.items())),
    }


def coverage(through, month, updated, covered_from=None):
    first = dt.date.fromisoformat(month + "-01")
    last = min(
        dt.date.fromisoformat(updated),
        first.replace(day=calendar.monthrange(first.year, first.month)[1]),
    )
    if not covered_from or covered_from > first.isoformat():
        return "unavailable"
    if through and through >= last.isoformat():
        return "complete"
    if through and through >= first.isoformat():
        return "partial"
    return "unavailable"


def calculate_profile(repositories, updated, owned, timings, estimator, login):
    windows = month_windows(updated)
    as_of_end = dt.datetime.combine(
        dt.date.fromisoformat(updated) + dt.timedelta(days=1), dt.time(), UTC
    )
    keys = ("low", "base", "high")
    public_through = (owned or {}).get("public_through_utc")
    private_through = (owned or {}).get("private_through_utc")
    monthly = {}
    for window in windows:
        month = window["month"]
        monthly[month] = {
            **window,
            "direction_calendar_days": window["elapsed_days"],
            "modeled_human_direction_hours": {
                key: window["direction_hours"] for key in keys
            },
            "skilled_human_equivalent_hours": dict.fromkeys(keys, 0.0),
            "public_prs_with_work": 0,
            "public_merged_pull_requests": 0,
            "public_authored_pr_commits": 0,
            "owned_public_commits": 0,
            "owned_private_commits": 0,
            "creation_month_proxy_prs": 0,
            "owned_public_coverage": coverage(
                public_through, month, updated, (owned or {}).get("public_from_utc")
            ),
            "owned_private_coverage": coverage(
                private_through, month, updated, (owned or {}).get("private_from_utc")
            ),
        }
    items, covered_digests, seen_prs = [], set(), {}
    proxy_prs = 0
    credited_shas = set()
    excluded_reused_prs = []
    reused_commit_entries = 0
    eligible = [
        (repo, pr)
        for repo in repositories
        for pr in repo.get("_merged_pull_requests", [])
        if pr.get("mergedAt")
    ]
    eligible.sort(
        key=lambda pair: (
            timestamp(pair[1]["mergedAt"]),
            pair[0]["full_name"].casefold(),
            int(pair[1]["number"]),
        )
    )
    for repo, pr in eligible:
        if not pr.get("mergedAt"):
            continue
        merged_at = timestamp(pr["mergedAt"])
        if merged_at >= as_of_end:
            continue
        key = (repo["full_name"].casefold(), int(pr["number"]))
        if key in seen_prs:
            if seen_prs[key] != pr:
                raise ValueError("Conflicting duplicate PR evidence")
            continue
        seen_prs[key] = pr
        for field in ("additions", "deletions", "changedFiles"):
            if int(pr[field]) < 0:
                raise ValueError("Negative PR statistics")
        item = estimator(repo, pr)
        item.pop("modeled_human_direction_hours", None)
        entry = (timings or {}).get("pull_requests", {}).get(pr["url"])
        if entry is not None and (
            entry["commit_count"] != len(entry["commits"])
            or entry["commit_count"] != int(pr["commits"]["totalCount"])
        ):
            raise ValueError("Incomplete PR commit timeline")
        commits = entry["commits"] if entry else []
        if len({row["sha"] for row in commits}) != len(commits):
            raise ValueError("Duplicate PR commit timestamp")
        author_months = Counter()
        all_non_merge = {
            commit["sha"] for commit in commits if commit["parent_count"] <= 1
        }
        own_commits = [
            commit
            for commit in commits
            if commit["parent_count"] <= 1
            and str(commit.get("author_login") or "").casefold() == login.casefold()
        ]
        duplicate_shas = all_non_merge & credited_shas
        reused_commit_entries += len(duplicate_shas)
        for commit in commits:
            if commit["parent_count"] > 1:
                continue
            covered_digests.add(digest(commit["sha"]))
            authored_at = timestamp(commit["authored_at"])
            if authored_at > merged_at:
                raise ValueError(
                    "Authored commit timestamp is later than its acceptance"
                )
            if commit in own_commits and commit["sha"] not in credited_shas:
                author_months[authored_at.strftime("%Y-%m")] += 1
        merge_month = merged_at.strftime("%Y-%m")
        if merge_month in monthly:
            monthly[merge_month]["public_merged_pull_requests"] += 1
        total_implementation = len(own_commits) if own_commits else len(all_non_merge)
        new_implementation = (
            sum(author_months.values())
            if own_commits
            else len(all_non_merge - credited_shas)
        )
        credited_shas.update(all_non_merge)
        if total_implementation and new_implementation == 0:
            excluded_reused_prs.append(
                {
                    "url": pr["url"],
                    "reason": "all identifiable implementation commits already credited to an earlier accepted PR",
                }
            )
            continue
        if total_implementation and new_implementation < total_implementation:
            item["heh_before_reuse_adjustment"] = dict(item["heh"])
            item["heh"] = {
                key: float(
                    (
                        Decimal(str(value)) * new_implementation / total_implementation
                    ).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                )
                for key, value in item["heh"].items()
            }
        item["reused_implementation_commit_entries_excluded"] = len(duplicate_shas)
        if author_months:
            basis = "equal shares per newly credited attributed non-merge PR commit, grouped by UTC authored month"
        else:
            created_at = timestamp(pr["createdAt"])
            if created_at > merged_at:
                raise ValueError("PR created after acceptance")
            author_months[created_at.strftime("%Y-%m")] = 1
            basis = "PR creation-month proxy; no attributed non-merge commit timing available"
        allocations = {
            key: allocate_hours(item["heh"][key], author_months) for key in keys
        }
        visible = sorted(set(author_months) & set(monthly))
        if not visible:
            continue
        if not commits or not any(
            str(c.get("author_login") or "").casefold() == login.casefold()
            and c["parent_count"] <= 1
            for c in commits
        ):
            proxy_prs += 1
        for month in visible:
            row = monthly[month]
            row["public_prs_with_work"] += 1
            if basis.startswith("PR creation"):
                row["creation_month_proxy_prs"] += 1
            else:
                row["public_authored_pr_commits"] += author_months[month]
            for key in keys:
                row["skilled_human_equivalent_hours"][key] += allocations[key][month]
        item["work_timing_basis"] = basis
        item["monthly_heh_allocation"] = {
            month: {key: allocations[key][month] for key in keys}
            for month in sorted(author_months)
        }
        items.append(item)
    counts = {"public": 0, "private": 0}
    overlap = 0
    for identity, receipt in (owned or {}).get("receipts", {}).items():
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("Invalid redacted commit digest")
        if set(receipt) != {"month", "visibility", "category", "heh"}:
            raise ValueError("Owned receipt contains unpublished identity fields")
        if identity in covered_digests:
            overlap += 1
            continue
        month = receipt["month"]
        if month not in monthly:
            continue
        visibility = receipt["visibility"]
        if visibility not in counts:
            raise ValueError("Invalid commit visibility")
        counts[visibility] += 1
        monthly[month][f"owned_{visibility}_commits"] += 1
        for key in keys:
            value = float(receipt["heh"][key])
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid owned-work HEH")
            monthly[month]["skilled_human_equivalent_hours"][key] += value
    for row in monthly.values():
        row["skilled_human_equivalent_hours"] = {
            key: round(value, 2)
            for key, value in row["skilled_human_equivalent_hours"].items()
        }
        complete = (
            row["owned_public_coverage"] == row["owned_private_coverage"] == "complete"
        )
        row["coverage_status"] = "complete" if complete else "partial"
        heh = row["skilled_human_equivalent_hours"]
        row["agentic_power_x"] = {
            key: (
                round(heh[key] / row["direction_hours"], 2)
                if complete or heh["base"]
                else None
            )
            for key in keys
        }
        row["repository_default_branch_commits"] = (
            row["owned_public_commits"] + row["owned_private_commits"]
        )
    heh = {
        key: round(
            sum(row["skilled_human_equivalent_hours"][key] for row in monthly.values()),
            2,
        )
        for key in keys
    }
    hours = sum(row["direction_hours"] for row in monthly.values())
    return {
        "schema_version": 3,
        "model_version": MODEL_VERSION,
        "updated_at_utc": updated,
        "basis": "Authored-work monthly HEH divided by two assumed human-direction hours per elapsed day",
        "scope": "Accepted public upstream PRs plus attributed default-branch commits in accessible personally owned non-fork repositories",
        "window": {
            "start": windows[0]["month"] + "-01",
            "through": updated,
            "calendar_months": 12,
        },
        "framework": {
            "name": "The Agentic Power Framework",
            "author": "Dr. Mark Allen / HeroForge.AI",
            "url": "https://heroforge-agentic-power.artful-fly-4358.chatgpt.site/",
            "formula": "AP = modeled HEH / assumed human direction hours",
        },
        "direction_calibration": {
            "hours_per_week": 14.0,
            "hours_per_day": 2.0,
            "operator_estimated_hours_per_week": dict.fromkeys(keys, 14.0),
            "allocation_method": "two hours per elapsed calendar day; independent of contribution volume",
        },
        "modeled_human_direction_hours": dict.fromkeys(keys, hours),
        "skilled_human_equivalent_hours": heh,
        "agentic_power_x": {key: round(heh[key] / hours, 2) for key in keys},
        "coverage_status": (
            "complete"
            if all(row["coverage_status"] == "complete" for row in monthly.values())
            else "partial"
        ),
        "aggregate_basis": "Modeled ratio for recorded work only; incomplete coverage is not an account-wide total.",
        "public_reused_commit_entries_excluded": reused_commit_entries,
        "public_prs_excluded_as_reused_implementation": excluded_reused_prs,
        "public_accepted_pull_requests": len(items),
        "public_accepted_merge_events": sum(
            row["public_merged_pull_requests"] for row in monthly.values()
        ),
        "repository_default_branch_non_merge_commits": sum(counts.values()),
        "owned_public_commits": counts["public"],
        "owned_private_commits": counts["private"],
        "repository_count": int((owned or {}).get("public_repository_count", 0))
        + int((owned or {}).get("private_repository_count", 0)),
        "owned_public_through_utc": public_through,
        "owned_private_through_utc": private_through,
        "creation_month_proxy_prs": proxy_prs,
        "excluded_owned_pr_overlap_commits": overlap,
        "engineer_weeks_at_40h": round(heh["base"] / 40, 1),
        "monthly": list(monthly.values()),
        "methodology": {
            "heh": "PR task-equivalence uses title category and change statistics; owned commits use a fixed conventional-title class. These are effort scenarios, not measured hours, time saved, or downstream value.",
            "attribution": "Estimate each accepted PR once; allocate its HEH across UTC authored months in proportion to its attributed non-merge commit counts, preserving every modeled cent. Use the PR creation month only when attributed commit timing is unavailable, and flag that proxy explicitly.",
            "acceptance": "Only merged public PRs enter public HEH; owned commits must be authored and present on the default branch. Merge-month acceptance counts are separate from work-month HEH.",
            "revision": "Later acceptance backfills the authored work month(s), even across the rolling boundary. New activity never reallocates another month's denominator. Current month is partial; evidence/model corrections may revise affected history.",
            "deduplication": "PRs are ordered by acceptance time, repository and number. An implementation commit is credited to the first accepted PR containing it; later PR estimates are reduced in proportion to their newly credited implementation commits, with no separate integration-effort credit. Owned commits are deduplicated by one-way SHA-256 digest and excluded if already covered by public PR work.",
            "coverage": "A public-only credential refreshes public owned work and retains dated private receipts. Coverage is evaluated per month. Missing private coverage is not evidence of zero private work.",
            "privacy": "Private names, URLs, code, raw SHAs, exact dates and commit text are not published. Opaque commit digests permit later public/private overlap checks without publishing private identities.",
            "direction": "User-provided assumption: approximately 14 hours/week, implemented as 2 hours/day. Full months use their actual calendar length; current month uses elapsed UTC dates inclusive.",
            "range": "Low/high curves vary HEH assumptions only, keeping direction at 2h/day. They are sensitivity scenarios, not confidence intervals.",
        },
        "work_items": sorted(
            items,
            key=lambda item: (item["merged_at"], item["project"], item["pull_request"]),
        ),
    }


def _collect_owned_history(
    token, updated, login, graphql, cached=None, include_private=False, exclusions=()
):
    """Collect authored commits from pinned default-branch heads, without search limits."""
    repositories, cursor, user_id, expected = [], None, None, None
    query = """query($login:String!, $cursor:String) {
      user(login:$login) { id repositories(first:100,after:$cursor,ownerAffiliations:[OWNER],isFork:false) {
        totalCount pageInfo { hasNextPage endCursor }
        nodes { nameWithOwner isPrivate isFork defaultBranchRef { target { ... on Commit { oid } } } }
      } }
    }"""
    while True:
        user = graphql(token, query, {"login": login, "cursor": cursor})["user"]
        user_id = user["id"]
        connection = user["repositories"]
        if expected is None:
            expected = int(connection["totalCount"])
        elif int(connection["totalCount"]) != expected:
            raise RuntimeError(
                "Owned repository inventory changed while paging; retry snapshot"
            )
        repositories.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]
    if (
        len(repositories) != expected
        or len({row["nameWithOwner"].casefold() for row in repositories}) != expected
    ):
        raise RuntimeError("Incomplete owned repository inventory")
    selected = [
        repo
        for repo in repositories
        if repo["defaultBranchRef"]
        and repo["nameWithOwner"].split("/", 1)[0].casefold() == login.casefold()
        and repo["nameWithOwner"].casefold() not in exclusions
        and not repo["isFork"]
        and (include_private or not repo["isPrivate"])
    ]
    raw = []
    for offset in range(0, len(selected), 20):
        batch = selected[offset : offset + 20]
        fields = []
        for index, repo in enumerate(batch):
            owner, name = repo["nameWithOwner"].split("/", 1)
            head = repo["defaultBranchRef"]["target"]["oid"]
            fields.append(
                f"r{index}: repository(owner:{json.dumps(owner)},name:{json.dumps(name)}) {{ object(oid:{json.dumps(head)}) {{ ... on Commit {{ history(first:100,author:{{id:{json.dumps(user_id)}}}) {{ totalCount pageInfo {{ hasNextPage endCursor }} nodes {{ oid authoredDate messageHeadline parents {{ totalCount }} }} }} }} }} }}"
            )
        result = graphql(token, "query { " + " ".join(fields) + " }", {})
        for index, repo in enumerate(batch):
            connection = result[f"r{index}"]["object"]["history"]
            nodes = list(connection["nodes"])
            total = int(connection["totalCount"])
            while connection["pageInfo"]["hasNextPage"]:
                owner, name = repo["nameWithOwner"].split("/", 1)
                head = repo["defaultBranchRef"]["target"]["oid"]
                page_query = """query($owner:String!, $name:String!, $head:GitObjectID!, $author:ID!, $cursor:String!) {
                  repository(owner:$owner,name:$name) { object(oid:$head) { ... on Commit {
                    history(first:100,after:$cursor,author:{id:$author}) {
                      totalCount pageInfo { hasNextPage endCursor }
                      nodes { oid authoredDate messageHeadline parents { totalCount } }
                    }
                  } } }
                }"""
                connection = graphql(
                    token,
                    page_query,
                    {
                        "owner": owner,
                        "name": name,
                        "head": head,
                        "author": user_id,
                        "cursor": connection["pageInfo"]["endCursor"],
                    },
                )["repository"]["object"]["history"]
                if int(connection["totalCount"]) != total:
                    raise RuntimeError("Pinned commit history changed while paging")
                nodes.extend(connection["nodes"])
            if len(nodes) != total or len({node["oid"] for node in nodes}) != total:
                raise RuntimeError("Incomplete pinned default-branch authored history")
            for node in nodes:
                raw.append(
                    {
                        "sha": node["oid"],
                        "repository": {
                            "full_name": repo["nameWithOwner"],
                            "private": repo["isPrivate"],
                            "fork": False,
                        },
                        "parents": [None] * int(node["parents"]["totalCount"]),
                        "commit": {
                            "author": {"date": node["authoredDate"]},
                            "message": node["messageHeadline"],
                        },
                    }
                )
        print(
            f"Owned default-branch histories: {min(offset+20,len(selected))}/{len(selected)} verified (identities redacted)",
            flush=True,
        )

    def dated(first, last):
        return [
            row
            for row in raw
            if first <= timestamp(row["commit"]["author"]["date"]).date() <= last
        ]

    aggregate = collect_owned_work(
        updated, login, dated, cached, include_private, exclusions
    )
    aggregate["collection"] = (
        "Complete GitHub GraphQL authored histories at pinned default-branch heads; author filter uses the account node ID. All history pages validated against totalCount before date filtering."
    )
    aggregate["default_branch_snapshots_verified"] = len(selected)
    return aggregate


def collect_owned_history(
    token, updated, login, graphql, cached=None, include_private=False, exclusions=()
):
    """Keep private API diagnostics out of public workflow logs."""
    try:
        return _collect_owned_history(
            token, updated, login, graphql, cached, include_private, exclusions
        )
    except Exception:
        if include_private:
            raise RuntimeError(
                "Owned-history collection failed; private details withheld"
            ) from None
        raise
