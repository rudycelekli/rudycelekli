# Agentic Power calculation

This is a model of task-equivalent work per assumed human-direction hour. It is
not a time-tracked productivity study or evidence of downstream benefit.

## Time window and direction

The chart always contains the current UTC calendar month and the preceding eleven.
A completed month uses two direction hours per calendar day: 60 hours in a 30-day
month, 62 in a 31-day month, and 58 in a leap-year February. The current month uses
dates through the as-of date, inclusive. This implements the operator's approximate
14-hours-per-week assumption without allocating direction according to output.

Each monthly AP is that month's modeled HEH divided by its direction hours. The
12-month figure divides total HEH by total direction; it is not an average of
monthly ratios. Denominators do not depend on the first contribution or later work.

## Public upstream work

An authored public PR becomes eligible only after merging. Its task-equivalent
hours are estimated once using the existing title-category and change-statistics
model. Those hours are allocated across the UTC authored months of its attributed
non-merge commits, in proportion to commit counts. Integer-cent allocation preserves
the estimate across all months, including months outside the displayed window.

Complete public commit timelines are retained in `data/public-pr-work-timing.json`.
The collector validates counts and uniqueness across every page. For PRs that hit
the 250-commit connection cap, it uses the paginated base-to-head comparison only
if its complete commit count matches the PR connection's reported total.

When there is no attributable non-merge commit timing, the PR creation month is an
explicitly counted timing proxy. It is not described as an actual work log.
Merge-month acceptance counts remain separate from work-month HEH.

PRs are ordered by acceptance timestamp, repository, and number. Identical
implementation SHAs receive credit in the first accepted PR containing them.
Later PR estimates are reduced in proportion to their newly credited
implementation commits; fully reused implementation receives no extra HEH.
Integration effort is not estimated separately. This prevents repeatedly counting
implementation, while retaining all accepted PR receipts in the contribution ledger.

## Personally owned repositories

The collector enumerates accessible, personally owned, non-fork repositories,
pins each default-branch head, and pages its complete account-attributed commit
history. Counts and uniqueness must reconcile before filtering to the rolling
authored-date window. Merge commits, the profile repository, and explicit authorship
exceptions are excluded. Other accounts' and organizations' repositories are outside
this lane, including employer/client organization repositories.

Owned commit HEH uses a documented conventional-title class, not lines changed:

| Class | Base modeled hours per commit |
| --- | ---: |
| Feature/research | 2.5 |
| Fix/refactor | 1.5 |
| Quality/build | 1.0 |
| Documentation/data | 0.75 |
| Maintenance/unclassified | 0.5 |

These are heuristic task-equivalence assumptions, not measured engineering time.
The PR model likewise uses heuristic title baselines, file and commit counts, and
nonlinear change size. Low/high scenarios vary HEH; direction remains two hours/day.
Sensitivity ranges are not statistical confidence intervals.

The owned-work file publishes only one-way SHA-256 commit digests, UTC month,
visibility, outcome class, and modeled hours. Private repository names, raw SHAs,
exact timestamps, URLs, code, and commit text are not published. Digests deduplicate
copies across owned repositories and exclude work already covered by public PRs.

## Revisions and incomplete coverage

Late acceptance backfills the authored work month(s). It does not move effort into
the merge month or rescale unrelated history. Evidence corrections or title/model
changes can revise affected months. This is a reconstruction, not an as-known-at-
the-time historical record or a timesheet.

Public and private owned lanes carry separate `from` and `through` bounds. A month
is complete only when both lanes cover its entire required interval. Unobserved months
are never filled with zeros or invented from a legacy aggregate. Partial coverage is marked
in the chart; a zero-work month with unavailable evidence has no AP point. An
aggregate with incomplete coverage is labeled recorded-work only, not an account-wide
total. The prior broad repository aggregate is retained as a legacy artifact and
never mixed into this approved scope.

## Automatic refresh

The existing twice-hourly GitHub Actions workflow refreshes public PRs and public
owned work. Manual and scheduled refreshes are serialized to avoid competing pushes. Accepted public PR timelines are cached; new PRs are collected as needed.
The rolling twelve-month window advances automatically.

Private owned histories can refresh automatically when the repository has an
`AP_REPOSITORY_TOKEN` Actions secret holding a read-only credential with access to
the chosen personally owned repositories. Restrict repository contents/metadata to
read access. Never put credentials in generated data, command output, or source.

Without that secret, public refreshes retain the previous redacted private receipts
and their original coverage bounds. The workflow does not relabel them fresh.
Private coverage becomes partial/unavailable where appropriate as the calendar moves.

Run local regression checks with:

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
```

### Refresh reliability

Scheduled and manual runs use `scripts/refresh_profile.py`. Collection runs in a
separate temporary checkout of the explicit serving inputs, with a 22-minute
limit. It cannot rewrite the published checkout while fetching evidence. A
completion receipt binds the attempt ID, evidence date and hashes of all 14
serving files. Before promotion, the supervisor checks every file, SVG syntax,
JSON structure, UTC dates, the contiguous 12-month window, direction hours,
monthly and aggregate ratios, coverage bounds and private receipt redaction.

Only a complete validated bundle is promoted. The workflow publishes it in one
Git commit. A collector failure, timeout or rejected bundle retains the prior
artifacts and publishes a small status note instead. `data/refresh-health.json`
records the attempt, last successful collection, served snapshot date and safe
failure category. A failed refresh still fails the Actions job after retention;
retention is not reported as fresh collection. Data older than two days is marked
stale when a failed run retains it. Private coverage keeps its actual collection
date even when the public refresh succeeds.

A per-checkout process lock and Actions concurrency group prevent overlapping
refreshes. Checkout changes detected before promotion abort the update; normal
Git push conflicts fail safely rather than force-pushing. Temporary output paths
are unique and a failed copy rolls back this run's writes. Raw collector
diagnostics are discarded rather than exposed in public workflow logs.

Run the same prepublication check locally with:

```sh
python scripts/refresh_profile.py --validate-only
python -m unittest discover -s scripts -p 'test_*.py'
```

This protects the published evidence from ordinary refresh failures. It cannot
guarantee GitHub availability, credentials, scheduled job execution, storage or
correctness of upstream data. The visible snapshot date remains the authority
when automation stops entirely. Retry a failed workflow after resolving its safe
failure category; do not edit dates to simulate a successful refresh. Private
collection still requires the separately configured read-only
`AP_REPOSITORY_TOKEN`; the supervisor does not create or broaden credentials.
