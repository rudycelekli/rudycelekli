"""Isolate collection, validate its serving bundle, and retain evidence on failure.

The supervisor deliberately emits only allowlisted failure codes: collector output
can contain private API diagnostics and must never be copied into public logs.
"""

import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

if __package__:
    from . import update_contributions as source
else:
    import update_contributions as source

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = tuple(
    str(p.relative_to(source.ROOT)) for p in source.published_artifact_paths()
)
HEALTH = "data/refresh-health.json"
START, END = "<!-- refresh-health:start -->", "<!-- refresh-health:end -->"


class InvalidBundle(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(root):
    return {
        name: (root / name).read_bytes() if (root / name).exists() else None
        for name in (*ARTIFACTS, HEALTH)
    }


def validate_bundle(root, receipt=None, attempt=None, earliest=None, latest=None):
    """Check complete files, dates, independent monthly arithmetic and redaction."""
    try:
        contents = {}
        for name in ARTIFACTS:
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise InvalidBundle("missing_artifact")
            contents[name] = path.read_bytes()
            if not contents[name].strip():
                raise InvalidBundle("empty_artifact")
            if name.endswith(".svg"):
                if (
                    ET.fromstring(contents[name]).tag
                    != "{http://www.w3.org/2000/svg}svg"
                ):
                    raise InvalidBundle("invalid_svg")
        data = {
            name: json.loads(raw)
            for name, raw in contents.items()
            if name.endswith(".json")
        }
        ap = data["data/agentic-power.json"]
        day = dt.date.fromisoformat(ap["updated_at_utc"])
        if data["data/contributions.json"]["updated_at_utc"] != day.isoformat():
            raise InvalidBundle("inconsistent_dates")
        if (earliest and day < earliest) or (latest and day > latest):
            raise InvalidBundle("unexpected_date")
        months = source.agentic_work.month_windows(day.isoformat())
        rows = ap["monthly"]
        if len(rows) != 12 or [r["month"] for r in rows] != [
            r["month"] for r in months
        ]:
            raise InvalidBundle("invalid_window")
        total_hours = 0
        for row in rows:
            first = dt.date.fromisoformat(row["month"] + "-01")
            next_month = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            days = (min(next_month, day + dt.timedelta(days=1)) - first).days
            hours = days * 2
            total_hours += hours
            if (
                row["direction_hours"] != hours
                or row["direction_calendar_days"] != days
            ):
                raise InvalidBundle("invalid_direction")
            if row["partial_month"] != (day < next_month - dt.timedelta(days=1)):
                raise InvalidBundle("invalid_partial_month")
            for key in ("low", "base", "high"):
                value = row["skilled_human_equivalent_hours"][key]
                ratio = row["agentic_power_x"][key]
                if not math.isfinite(value) or value < 0:
                    raise InvalidBundle("invalid_heh")
                expected = round(value / hours, 2)
                if ratio is None:
                    if row["coverage_status"] == "complete" or value:
                        raise InvalidBundle("invalid_missing_value")
                elif ratio != expected:
                    raise InvalidBundle("invalid_ratio")
        for key in ("low", "base", "high"):
            total = round(
                sum(r["skilled_human_equivalent_hours"][key] for r in rows), 2
            )
            if (
                ap["skilled_human_equivalent_hours"][key] != total
                or ap["modeled_human_direction_hours"][key] != total_hours
                or ap["agentic_power_x"][key] != round(total / total_hours, 2)
            ):
                raise InvalidBundle("invalid_totals")
        owned = data["data/owned-repository-work.json"]
        for lane in ("public", "private"):
            through = owned.get(lane + "_through_utc")
            first = owned.get(lane + "_from_utc")
            if bool(first) != bool(through):
                raise InvalidBundle("incomplete_coverage_bounds")
            if first and not (
                dt.date.fromisoformat(first) <= dt.date.fromisoformat(through) <= day
            ):
                raise InvalidBundle("invalid_coverage_bounds")
        for key, item in owned["receipts"].items():
            if not re.fullmatch(r"[a-f0-9]{64}", key):
                raise InvalidBundle("invalid_digest")
            if set(item) != {"month", "visibility", "category", "heh"}:
                raise InvalidBundle("unexpected_private_fields")
            if set(item["heh"]) != {"low", "base", "high"} or any(
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
                for value in item["heh"].values()
            ):
                raise InvalidBundle("invalid_private_heh")
            if (
                not re.fullmatch(r"\d{4}-\d{2}", item["month"])
                or item["visibility"] not in ("public", "private")
                or item["category"]
                not in {
                    "feature_and_research",
                    "fix_and_refactor",
                    "quality",
                    "knowledge",
                    "maintenance",
                }
            ):
                raise InvalidBundle("invalid_private_receipt")
        if receipt is not None:
            proof = json.loads(receipt.read_text())
            expected = {name: digest(raw) for name, raw in contents.items()}
            if (
                proof["attempt_id"] != attempt
                or proof["snapshot_date"] != day.isoformat()
                or proof["sha256"] != expected
            ):
                raise InvalidBundle("invalid_completion_receipt")
        return day
    except InvalidBundle:
        raise
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError, ZeroDivisionError):
        raise InvalidBundle("invalid_bundle") from None


def health_note(readme, health):
    status = health["status"]
    text = (
        f"Evidence snapshot: {health['snapshot_date'] or 'unavailable'} UTC. "
        + (
            "Refresh delayed; showing the previous validated snapshot. "
            if status == "delayed"
            else (
                "Evidence is stale; the last refresh failed. "
                if status == "stale"
                else (
                    "No validated snapshot is available. "
                    if status == "unavailable"
                    else ""
                )
            )
        )
        + f"Private-work coverage through {health['private_through_utc'] or 'unavailable'}. "
        + "[Refresh status](./data/refresh-health.json) · "
        "[Refresh runs](https://github.com/rudycelekli/rudycelekli/actions/workflows/refresh-contribution-stats.yml)"
    )
    block = f"{START}\n<sub>{text}</sub>\n{END}"
    if START in readme and END in readme:
        return re.sub(
            re.escape(START) + r".*?" + re.escape(END),
            lambda _: block,
            readme,
            flags=re.S,
        )
    return readme.replace(
        "## Agentic Power over time", block + "\n\n## Agentic Power over time", 1
    )


def replace_file(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".refresh-tmp", dir=path.parent
    )
    os.close(fd)
    temporary = Path(filename)
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def promote(root, before, updates):
    """Refuse concurrent edits and roll back owned writes on a copy failure."""
    if snapshot(root) != before:
        raise InvalidBundle("concurrent_edit")
    written = []
    try:
        for name, raw in updates.items():
            replace_file(root / name, raw)
            written.append(name)
    except OSError:
        for name in reversed(written):
            if (root / name).read_bytes() != updates[name]:
                continue  # Never roll back another writer's bytes.
            if before[name] is None:
                (root / name).unlink(missing_ok=True)
            else:
                replace_file(root / name, before[name])
        raise


def _run_refresh(root, runner, timeout):
    started = dt.datetime.now(dt.timezone.utc)
    before = snapshot(root)
    failure = None
    updates = {}
    day = None
    try:
        with tempfile.TemporaryDirectory(prefix="profile-refresh-") as temporary:
            stage = Path(temporary)
            for name, raw in before.items():
                if raw is not None:
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
            # Only source scripts are copied: no Git metadata, .env, or credentials.
            (stage / "scripts").mkdir()
            for path in (root / "scripts").glob("*.py"):
                shutil.copyfile(path, stage / "scripts" / path.name)
            attempt = uuid.uuid4().hex
            receipt = stage / "completion.json"
            env = {
                **os.environ,
                "PROFILE_REFRESH_RECEIPT_PATH": str(receipt),
                "PROFILE_REFRESH_ATTEMPT_ID": attempt,
            }
            # A file bounds memory consumption; diagnostics are discarded privately.
            with tempfile.TemporaryFile() as log:
                result = runner(
                    [sys.executable, str(stage / "scripts/update_contributions.py")],
                    cwd=stage,
                    env=env,
                    stdout=log,
                    stderr=log,
                    timeout=timeout,
                    check=False,
                )
            if result.returncode:
                raise RuntimeError("collection_failed")
            day = validate_bundle(
                stage,
                receipt,
                attempt,
                started.date(),
                dt.datetime.now(dt.timezone.utc).date(),
            )
            updates = {name: (stage / name).read_bytes() for name in ARTIFACTS}
    except subprocess.TimeoutExpired:
        failure = "collection_timeout"
    except InvalidBundle:
        failure = "validation_failed"
    except RuntimeError:
        failure = "collection_failed"
    except OSError:
        failure = "staging_failed"
    if failure:
        updates = {}
        try:
            day = validate_bundle(root, latest=started.date())
        except InvalidBundle:
            day = None
    if snapshot(root) != before:
        print("Refresh failed: concurrent_edit; checkout was preserved.")
        return 1
    try:
        old_health = json.loads(before[HEALTH]) if before[HEALTH] else {}
        if not isinstance(old_health, dict):
            old_health = {}
    except (ValueError, TypeError):
        old_health = {}
    try:
        owned = json.loads(
            updates.get("data/owned-repository-work.json")
            or before["data/owned-repository-work.json"]
            or b"{}"
        )
        if not isinstance(owned, dict):
            owned = {}
    except (ValueError, TypeError):
        owned = {}
    health = {
        "schema_version": 1,
        "status": (
            "unavailable"
            if day is None
            else (
                "stale"
                if failure and (started.date() - day).days > 2
                else "delayed" if failure else "updated"
            )
        ),
        "last_attempt_utc": started.isoformat(),
        "last_successful_collection_utc": (
            old_health.get("last_successful_collection_utc")
            if failure
            else dt.datetime.now(dt.timezone.utc).isoformat()
        ),
        "snapshot_date": day.isoformat() if day else None,
        "private_through_utc": owned.get("private_through_utc") if day else None,
        "failure_kind": failure,
    }
    updates[HEALTH] = (json.dumps(health, indent=2, sort_keys=True) + "\n").encode()
    if day is not None:
        readme = (updates.get("README.md") or before["README.md"]).decode()
        updates["README.md"] = health_note(readme, health).encode()
    try:
        promote(root, before, updates)
    except (InvalidBundle, OSError):
        print("Refresh failed: publication_failed; no collection was published.")
        return 1
    print(
        "Refresh "
        + (
            "failed: " + failure + "; retained evidence."
            if failure
            else "validated; complete bundle ready to commit."
        )
    )
    return 1 if failure else 0


def run_refresh(root=ROOT, runner=subprocess.run, timeout=1320):
    # Cross-process lock covers snapshot, staging, validation and promotion.
    # Outside the checkout so it cannot be committed or copied to staging.
    lock_path = Path(tempfile.gettempdir()) / (
        "profile-refresh-" + digest(str(root.resolve()).encode()) + ".lock"
    )
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Refresh skipped: another supervisor owns this checkout.")
            return 1
        return _run_refresh(root, runner, timeout)


if __name__ == "__main__":
    if "--validate-only" in sys.argv:
        validate_bundle(ROOT, latest=dt.datetime.now(dt.timezone.utc).date())
        print("Serving bundle validated.")
    else:
        sys.exit(run_refresh())
