# Handoff: runner chunk 5 (unblocker, sibling + amendment reviewers, project-config, CLI knobs, pr_url, worktree prune)

Date: 2026-05-20
Branch: `feature/runner-chunk-5` (off `main` at `5a00e74`)
Author: orchestrator pass (Drew to review)

## What this chunk is

Chunk 4 landed the merge gate (push + open PR via gh; tier 1-2
auto-merge), the canonical YAML loaders, the real `_is_eligible`, the
forensic-worktree reaper, the boot-time worker-alive check, and the
AC-amendment routing. The seven chunk-4 follow-on items the handoff
named are this chunk: a poll-for-merged unblocker, a sibling-agent
reviewer for tier 3-4 PRs, an AC-amendment review automation, a
per-project `project.yaml` plumbing with hot reload, CLI flags for
every chunk-4 + chunk-5 knob, a promoted `pr_url` column, and a
defensive `git worktree prune` sweep.

Branch started fresh off main at `5a00e74` (the chunk-4 merge,
verified at session start: `git log --oneline -3` showed `5a00e74
Merge pull request #8 from Ginkobaloba/feature/runner-chunk-4`). All
git operations went through PowerShell on the Windows host per
`C:\dev\SESSION_PROTOCOL.md` section 7.

## What shipped

### 1. `pr_url` promoted column (chunk-4 documented gap, item 6)

**Schema:** `runner/src/cards_runner/store/schema.py` adds a `pr_url`
column to the SQLite and MySQL/Dolt DDL. A new `ADDED_COLUMNS` table
plus an `added_column_alters(dialect)` helper drives a small migration
that the repository base class runs on every `initialize_schema()`:
for a brand-new database the `CREATE TABLE` already carries the
column and the per-column existence check skips the ALTER; for an
existing chunk-4-era database the ALTER fires once.

**Repository:** `_SqlCardRepository._apply_added_columns()` iterates
`ADDED_COLUMNS` and asks the subclass (`_column_exists`) whether the
column is already there. SQLite uses `PRAGMA table_info(cards)`;
Dolt uses `INFORMATION_SCHEMA.COLUMNS`. Each migration is idempotent
and runs in the same transaction as the `CREATE TABLE`s.

**Model:** `CardRecord.pr_url` is a typed attribute alongside the
other promoted columns, in `_PROMOTED_FIELD_NAMES`, in projection's
`_PROMOTABLE_ATTRS`, and in the canonical frontmatter key order. The
merge gate's `_apply_merge_outcome` now writes `pr_url` into the
`fields` dict, so a verifier-pass card lands the PR URL on the row
without any extra plumbing.

### 2. `project.yaml` plumbing + mtime hot reload (item 4)

**Module:** `runner/src/cards_runner/common/project_config.py`.

`ProjectConfig` is a frozen dataclass with optional sub-configs:
`ReviewerConfig` (sibling and amendment reviewers, with enabled /
model_id / label / cost_cap_usd / prompt_extra), `MergeGateRelaxation`
(tier-3/4 auto-merge opt-in plus an alt PR base branch), and the
verifier / cascade knobs the contract names
(`subjective_cascade_disabled`, `skip_confidence_threshold`,
`escalation_threshold`, `max_escalations`). Every field is
`Optional`; consumers fall back to a daemon default when `None`.

**Loader:** `load_project_config(path, strict=False)`. Missing file
or malformed YAML returns the empty default; `strict=True` raises
`ProjectConfigError` for CLI use. The loader degrades gracefully
because the daemon should never crash on an operator-supplied YAML
typo.

**Hot reload:** `ProjectConfigLoader` polls the file's mtime once
per daemon tick; a bumped mtime triggers a re-read. Windows has no
SIGHUP, so file-mtime polling is the portable equivalent (~one
`stat()` per tick).

**Wiring:** The daemon constructs a loader at boot (path comes from
`cfg.project_config_path` or `<todo_root>/project.yaml`) and calls
`reload_if_changed()` at the top of every tick. Live reads pass
`self.project_config` into the merge gate's `apply()` and into
`evaluate_eligibility()` so a mid-run change takes effect on the
next card processed.

**Eligibility:** `_resolve_story_source_path` now also reads the
project config's `story_source_path` when the card frontmatter omits
it. Chunk 4's TODO ("chunk 5 will add one if it turns out cards
routinely omit the field") is gone.

**Merge gate:** `decide_gate` accepts a `MergeGateRelaxation`. Tier
3-4 routes through `auto` when the project flips
`merge_gate.auto_merge_tier_3_4: true`; `pin_required` still wins
over relaxation per the contract.

### 3. Poll-for-merged unblocker (item 1)

**Module:** `runner/src/cards_runner/daemon/unblocker.py`.

`unblock_merged_cards(repo=, gh=, cfg=, ...)` walks `blocked` cards
whose `merge_status in {open, requires_review}`, runs `gh pr view
--json state,mergedAt` on each (via the existing `GhRunner.view_pr`),
and transitions to `done` with `merge_status=merged` when GitHub
reports MERGED. A non-merged state is logged as `still_pending` and
left put; a card with no `pr_url` returns `skipped_no_url`; a gh
failure returns `skipped_gh_failure`.

`merge_status=conflict` is intentionally NOT visited -- a conflict
needs a human rebase before it can merge. Per-decision shape lets
the tick summary count `unblocked_to_done` and lets tests assert the
exact reason for each card.

The unblocker is off by default (`pr_unblock_enabled=False`); the
CLI flag `--pr-unblock` flips it on, typically alongside `--pr-gate`.

**gh wrapper:** `daemon/pr_lifecycle.py` got a new `view_pr` method
on `SubprocessGhRunner` (parses the JSON into `result.parsed`) plus
mirrored stubs in `NullGhRunner`. The `_run` helper accepts
`cwd=None` so a fully-qualified PR URL can be polled without a cwd.

### 4. Sibling-agent reviewer (item 2)

**Module:** `runner/src/cards_runner/daemon/sibling_reviewer.py`.

`run_sibling_reviews(...)` walks `blocked/requires_review` cards.
For each card with a fresh `pr_url`:

1. Reads the PR diff via `gh.pr_diff(identifier=pr_url)`.
2. Calls the configured `SiblingReviewerClient.review(...)` with the
   card body, the diff, and the project's `ReviewerConfig`.
3. Posts the verdict via `gh pr review` (one of `--approve`,
   `--request-changes`, `--comment`).
4. On `approve`, additionally fires `gh pr merge --auto
   --delete-branch` so the unblocker can promote the card once
   GitHub auto-merges.
5. Writes `signals/sibling_reviews/<card_id>.json` so the next tick
   skips this PR until a new one opens.

**Reviewer clients** are pluggable:

- `StaticSiblingReviewerClient` returns scripted decisions; tests use
  it, and operators who want the runner to write markers without ever
  calling an LLM can wire it in production too.
- `AnthropicSiblingReviewerClient(client=anthropic_client)` issues a
  small one-shot `messages.create` call with a system prompt that
  instructs the model to emit a fenced YAML block with `decision`,
  `confidence`, and `reasoning`. The parser tolerates an unfenced
  block; a parse failure degrades to `comment` with the raw text as
  reasoning. `approve` decisions below confidence 0.7 are downgraded
  to `comment`.

The reviewer is gated by BOTH a host knob
(`cfg.sibling_reviewer_enabled`, set via `--sibling-reviewer`) AND
the project knob (`project.yaml > reviewers.sibling.enabled`). The
dual toggle is deliberate: the host operator owns whether any
reviewer calls happen at all; the project owns whether this
particular project wants them.

Per the contract the runner never edits AC; the sibling reviewer's
voice is a marker + a gh review + (on approve) a `gh pr merge
--auto` that GitHub queues. No card transitions until the unblocker
sees the merge land.

### 5. AC-amendment review automation (item 3)

**Module:** `runner/src/cards_runner/daemon/amendment_reviewer.py`.

`run_amendment_reviews(...)` walks `amendments` cards. For each:

1. Skips when the marker `signals/amendment_reviews/<card_id>.json`
   already exists.
2. Parses the `change_request:` YAML block from the card body
   (`extract_change_request_block`). A card in `amendments` without
   one is logged and skipped (the contract requires the executor to
   write the block before flipping status).
3. Calls the same `SiblingReviewerClient` interface the sibling
   reviewer uses, passing the change-request text in place of a PR
   diff -- the reviewer reads it as the "change to evaluate". This
   reuses the existing client + parser instead of building a parallel
   protocol.
4. Routes per the verdict:

   - **approve.** Routes the card to `blocked` with
     `merge_status=amendment_approved`. Chunk 5 deliberately does
     NOT auto-edit AC; the contract authorizes a delegated reviewer
     to do so, but automated AC editing is brittle enough to defer
     to a follow-on `auto_edit_ac: true` mode. A human (or that
     follow-on) finalizes the edit and moves the card back to
     `backlog`.
   - **request_changes.** Appends a `change_request_decision:` block
     to the body with the reviewer's reasoning and routes the card
     back to `active` so the executor resumes against the original
     AC. The `change_request:` block stays as audit trail per the
     contract.
   - **comment.** No transition; marker stays on disk so the
     reviewer does not re-spend tokens next tick. The card stays in
     `amendments` for human follow-up.

Both knobs (`cfg.amendment_reviewer_enabled` host-side,
`project.yaml > reviewers.amendment.enabled` project-side) are off
by default.

### 6. CLI flags for every chunk-4 / chunk-5 knob (item 5)

**Module:** `runner/src/cards_runner/cli/__main__.py`.

Added to `cards-runner start`:

- `--pr-gate` -- enable the chunk-4 tier-aware merge gate.
- `--pr-unblock` -- enable the chunk-5 poll-for-merged unblocker.
- `--sibling-reviewer` -- enable the chunk-5 tier-3/4 reviewer.
- `--amendment-reviewer` -- enable the chunk-5 amendments reviewer.
- `--worktree-prune` + `--worktree-prune-interval-sec` --
  the chunk-5 prune sweep.
- `--gh PATH`, `--git PATH` -- override the binaries.
- `--auto-merge-strategy {squash,merge,rebase}` -- chunk-4 strategy.
- `--no-boot-alive-check` -- skip the chunk-4 boot-alive check.
- `--forensic-ttl-hours N` -- chunk-4 reaper TTL override.
- `--pr-base BRANCH` -- daemon-level default PR base.
- `--project-config PATH` -- explicit project.yaml override.

`_cmd_start` builds a `cfg_kwargs` dict, applies CLI overrides only
when the operator passed one (otherwise `DaemonConfig`'s default
applies), and constructs the config. Defaults match the chunk-4
state: every new knob is `False` unless the operator opts in.

### 7. `git worktree prune` sweep (item 7)

**Module:** `runner/src/cards_runner/daemon/worktree.py`,
`prune_git_worktrees(project_dir, expire_after=None)`.

Defensive cleanup. The chunk-4 reaper deletes `_runs/<attempt>/`
directories; that drops the filesystem worktree but leaves the
`.git/worktrees/<id>/` administrative entries the project repo
tracks. `git worktree prune -v` is the official cleanup verb for
those entries.

The daemon's `_maybe_prune_git_worktrees()` walks the distinct
`project` fields on the live cards and runs the prune on each at
most every `worktree_prune_interval_sec` (default 3600). Off by
default; `skip_worktree=True` daemons are no-ops regardless.
Failures are logged but never raised -- this is a defensive sweep,
not a correctness requirement.

## What is verified

`python -m pytest tests/` with `CARDS_DOLT_BIN` set: **366 passed,
0 skipped** on the Windows host. Without `CARDS_DOLT_BIN`:
**337 passed, 29 skipped** (the Dolt-parametrized half of the
chunk-2a store suite).

Chunk-4 baseline was 276/247 + 29. Chunk 5 adds **90 new SQLite
tests**. Zero regressions.

`ruff check src/ tests/` clean.

New test files:

- `test_project_config.py` (13 tests) -- defaults, partial / full
  YAML, missing file (strict + non-strict), malformed YAML, mtime
  hot reload, loader path None, resolve_project_config_path
  precedence.
- `test_pr_url_column.py` (6 tests) -- column present on fresh DB,
  ALTER on legacy DB without dropping data, idempotent re-init,
  projection round trip, merge-gate end-to-end writes pr_url onto
  the row, `query_cards` returns it.
- `test_unblocker.py` (8 tests) -- disabled returns empty,
  skipped_no_url / skipped_gh_failure / still_pending, MERGED
  transitions the card to done with the merged event payload,
  requires_review variant also unblocks, conflict status is not
  visited, `split_decisions` helper.
- `test_sibling_reviewer.py` (12 tests) -- both knobs off no-op,
  no pr_url skips, diff failure skips, approve path posts review +
  fires merge, marker idempotency, request_changes does not fire
  merge, event emission, four YAML-decision-parsing edge cases.
- `test_amendment_reviewer.py` (12 tests) -- both knobs off no-op,
  missing change_request skips, approve routes to blocked +
  amendment_approved, deny appends change_request_decision and
  routes to active, comment leaves card put, marker idempotency,
  event emission, change_request block extraction (fenced and
  unfenced), multiline reasoning append.
- `test_chunk5_pr_lifecycle.py` (12 tests) -- view_pr arg
  construction + JSON parse + failure path, pr_diff, pr_review
  decision translation (`approve`/`request-changes`/`comment` plus
  unknown fallback), NullGhRunner refuses the new methods,
  cwd=None handling.
- `test_worktree_prune.py` (8 tests) -- non-git dir returns None,
  prune is called when git is present, expire flag passes through,
  CalledProcessError and TimeoutExpired are swallowed, daemon
  skips when knob is off or skip_worktree is True, runs when both
  conditions allow, rate-limited by interval.
- `test_chunk5_cli.py` (12 tests) -- every new CLI flag wires
  through to its DaemonConfig field; defaults match the chunk-4
  baseline; explicit overrides take effect.
- `test_chunk5_integration.py` (7 tests) -- daemon-level wiring:
  relaxation routes tier-3 to auto, pin still wins, eligibility
  uses project_config.story_source_path, mtime hot reload picks
  up new project.yaml values mid-run, injected reviewer clients,
  tick summary has the new keys, merge gate uses project
  pr_base_branch override.

## Decisions baked in

**`pr_url` is promoted, not denormalized.** The chunk-4 lifecycle
event payload still carries it; the new column is a queryable
shortcut for the dashboard and the unblocker. Storing it twice keeps
a single source-of-truth controversy off the table -- the event log
is the canonical write, the column is the read shortcut.

**Schema migrations live in a small table, not a versioned tool.**
`ADDED_COLUMNS` is a list of `(table, column, sqlite_type,
mysql_type)` tuples; `initialize_schema` runs them after the CREATE
TABLE pass with a per-column existence check so re-runs are no-ops.
A real migration tool (down-migrations, ordered numbered scripts,
explicit history table) is overkill for "we added one column"; we
can grow into it when the schema starts moving faster.

**`pr_unblock` and the reviewers are off-by-default and dual-gated.**
The host knob (`cfg.pr_unblock_enabled` /
`cfg.sibling_reviewer_enabled` / `cfg.amendment_reviewer_enabled`)
is the operator saying "yes the daemon may make these gh / LLM
calls"; the project knob (`project.yaml > reviewers.sibling.enabled`
etc.) is the project saying "and yes I want them for this repo".
Either side off keeps the sweep silent. Drew's instruction (the
runner must not silently call paid APIs because chunk-4 wired some
defaults) is honored.

**The amendment reviewer does NOT auto-edit AC in chunk 5.** The
contract authorizes a delegated reviewer to do so, but automated AC
editing has multiple plausible failure modes (wrong item indexed,
wrong YAML shape, drift between the change_request text and the
original item). Chunk 5 makes the approve case a routing decision
("a human or auto-edit mode finalizes the edit"); chunk 6 can add
the optional `auto_edit_ac: true` mode once we have a reviewer that
emits the exact replacement item structure under contract.

**Project config hot-reload uses mtime polling, not SIGHUP.** Windows
has no SIGHUP; the file-mtime poll is the portable equivalent. The
cost is one `stat()` per tick (cheap); the benefit is operators can
edit `project.yaml` without restarting the daemon, on either OS,
with the same plumbing.

**Sibling reviewer fires `gh pr merge --auto` on approve.** A pure
"post a review and stop" reviewer would require GitHub branch
protection to be configured for auto-merge-on-approval; we cannot
assume that. Firing the `--auto` flag ourselves keeps the runner
self-sufficient: the merge will happen the moment branch protection
clears, just like the tier-1/2 auto-merge path. If a project does
want the human-in-the-loop merge after sibling approval, they leave
the host knob off and merge manually.

**Worktree prune is rate-limited per daemon, not per project.** A
single `_last_prune_at` covers the whole daemon. The alternative
(per-project last-prune timestamps) is more precise but adds state
the daemon shouldn't be holding -- the contract says the runner is
stateless, so cheap-and-good-enough wins. The interval defaults to
3600 (hourly).

**Migration column existence check uses dialect-specific SQL.** The
SQLite path uses `PRAGMA table_info`; the Dolt path uses
`INFORMATION_SCHEMA.COLUMNS`. Both are stable and parameterizable,
and they sidestep the `SHOW COLUMNS LIKE` quirk where the placeholder
becomes a LIKE pattern rather than an equality match.

## What is intentionally NOT in chunk 5

- **`auto_edit_ac` mode for the amendment reviewer.** Approve still
  routes to `blocked/amendment_approved`; a human (or a follow-on
  mode that emits a structured AC patch) finalizes. The contract
  permits the reviewer to edit AC when explicitly delegated; we just
  haven't built the structured-output channel yet.
- **A reviewer for `awaiting_standup_review` cards.** The contract
  leaves these to a human; chunk 5 does not change that. Sibling
  reviewer covers tier 3-4 PRs only.
- **Per-card cost tracking for the reviewer.** The reviewer's spend
  is bounded by `max_tokens=1024` per call; integrating it with the
  worker_stub cost governor (so a reviewer call counts toward the
  card's `cost_cap_usd`) would tie chunk-5 to the chunk-2b-ii cost
  module in a way that complicates the daemon's tick path. Leaving
  it for a chunk-6 cost-attribution pass.
- **Per-project `gh` binary.** `cfg.gh_path` is host-wide today; a
  project that wants a pinned gh version would need to surface that
  through `project.yaml`. Not yet wired.
- **`gh pr merge --auto` via project-config strategy override.** The
  daemon-level `auto_merge_strategy` flows through; per-card override
  via frontmatter is reserved (the sibling reviewer reads
  `field_value("auto_merge_strategy")` if present and falls back).
- **A live end-to-end test of the gh subprocess wrappers.** Every
  test still mocks `subprocess.run` / uses a `FakeGhRunner`. A real
  gh smoke (push throwaway branch, open + close a draft PR, poll for
  state) belongs alongside chunk-6 prep.
- **`mypy --strict` on the new modules.** Same cause prior handoffs
  named. Code is written to strict standards; pyproject still keeps
  `strict = true`.

## Chunk 6, in order

1. **`auto_edit_ac: true` mode for the amendment reviewer.** A
   structured-output reviewer (JSON schema for the replacement AC
   item with the contract's provenance fields) plus a small
   AC-editor that splices it into the body, transitions the card
   back to `backlog`. Behind a project-config opt-in.
2. **Reviewer cost attribution.** The sibling and amendment
   reviewers should count their token use against the card's
   `actual_tokens` and (optionally) the card's `cost_cap_usd`. Today
   the reviewer's spend is invisible to the cost governor.
3. **Live `gh` smoke.** A `tests/manual/` script (not in pytest)
   that runs against a real GitHub repo and confirms the wrappers
   work end-to-end. Belongs in `tests/manual/smoke_gh.py`.
4. **Real `gh pr view` via a worktree.** `view_pr` accepts an
   optional `worktree=` argument. The unblocker passes None today
   (resolves the repo from the URL); a project that pins a specific
   remote may want the cwd locked to the worktree.
5. **`signals/sibling_reviews/<card_id>.json` and
   `signals/amendment_reviews/<card_id>.json` cleanup.** The
   forensic reaper does not touch the signals dir today. Old markers
   accumulate. A small sweep that drops markers older than N hours
   when the card has reached a terminal state would keep the dir
   tidy.
6. **A `cards-runner doctor` subcommand.** Reports the resolved
   binaries (gh, git, dolt), the project config path + contents,
   the schema migration status (which `ADDED_COLUMNS` are present),
   and a per-knob "are you on or off" summary. The chunk-3 / chunk-4
   handoffs both wanted it; chunk 5 left it deferred again because
   the CLI surface was already churning.
7. **A small per-project history of reviewer marker decisions.** The
   markers are point-in-time; aggregating them into a queryable
   "what did the reviewer approve last week" view (probably via a
   `signals` -> store sync) would surface drift in the reviewer's
   judgment.

## Environment notes for the next engineer

- All git operations went through PowerShell on the Windows host.
  This worktree (`feature/runner-chunk-5`) is at
  `C:\Users\Drama\Desktop\Claude\agile-cards\bold-chatterjee-1ec07b`;
  the main checkout at `C:\dev\agile-cards` was fast-forwarded to
  `origin/main` (chunk 4) at session start so local main and remote
  agree.
- `anthropic` (0.103.1) is on the host. `dolt` is at
  `C:\Users\Drama\AppData\Local\Programs\Dolt\dolt-windows-amd64\bin\dolt.exe`
  with `CARDS_DOLT_BIN` set at user scope. To run the suite with the
  Dolt half:
  `$env:CARDS_DOLT_BIN = [Environment]::GetEnvironmentVariable("CARDS_DOLT_BIN", "User")`
  before `python -m pytest tests/`.
- `gh` CLI is at `C:\Program Files\GitHub CLI\gh.exe`, version 2.91.0.
  The chunk-5 PR was opened with it.
- `feature/runner-chunk-5` branches off `main` at `5a00e74` (the
  chunk-4 merge commit). Hold the PR for Drew's review; do NOT
  auto-merge.

## How to run

```powershell
# From a fresh PowerShell session:
cd C:\Users\Drama\Desktop\Claude\agile-cards\bold-chatterjee-1ec07b\runner
$env:CARDS_DOLT_BIN = [Environment]::GetEnvironmentVariable("CARDS_DOLT_BIN", "User")
$env:PYTHONPATH = "src"
python -m pytest tests/ -q                  # 366 pass, 0 skip with dolt

# Without dolt:
Remove-Item Env:\CARDS_DOLT_BIN
python -m pytest tests/ -q                  # 337 pass, 29 skip

# Chunk-5 production-shape daemon (every knob opted in):
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
cards-runner start `
  --todo-root C:\dev\todo `
  --invoker sdk-tools `
  --pr-gate --pr-unblock `
  --sibling-reviewer --amendment-reviewer `
  --worktree-prune `
  --project-config C:\dev\todo\project.yaml

# Sample project.yaml that exercises every chunk-5 knob:
# story_source_path: docs/story.md
# verifier:
#   subjective_cascade_disabled: false
#   skip_confidence_threshold: 0.92
# cascade:
#   escalation_threshold: 0.6
#   max_escalations: 2
# reviewers:
#   sibling:
#     enabled: true
#     model: claude-sonnet-4-6
#     label: agile-cards-sibling-1
#     cost_cap_usd: 1.00
#     prompt_extra: "Lean on the project's CODING_STYLE.md."
#   amendment:
#     enabled: true
#     model: claude-haiku-4-5-20251001
#     label: agile-cards-amendment-1
# merge_gate:
#   auto_merge_tier_3_4: false
#   pr_base_branch: main
```

End of handoff.

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then the project
`RUNNER_CONTRACT.md` / `README.md`, then this file, then run `vstart`.
