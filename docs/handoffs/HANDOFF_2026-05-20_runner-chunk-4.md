# Handoff: runner chunk 4 (merge gates, gh CLI, canonical config, eligibility, reaper, alive check, amendments)

Date: 2026-05-20
Branch: `feature/runner-chunk-4` (off `main` at `9cf53bb`)
Author: orchestrator pass (Drew to review)

## What this chunk is

Chunk 3 landed the cold-read verifier and the executor tool belt;
clean verifier-PASS cards transitioned straight to `done` and the gh
CLI / merge gate side of the contract was deliberately deferred. The
chunk 3 handoff named eight chunk 4 line items; this chunk delivers
all eight plus the live smoke that proves `--invoker sdk-tools` works
against the real SDK.

Branch started fresh off main at `9cf53bb` (the chunk-3 merge commit,
verified at session start). All git operations went through PowerShell
on the Windows host per `C:\dev\SESSION_PROTOCOL.md` section 7.

## What shipped

### 1. Canonical `tier_map_claude.yaml` + `tier_pricing.yaml` wiring

**Module:** `runner/src/cards_runner/common/canonical_config.py`.

Replaces the embedded `_POINTS_TO_TIER` stand-in in
`sdk_invoker.py` and the embedded `_DEFAULT_PRICING` table in
`worker_stub/cost.py`. Path resolution order:

1. Explicit `path=` argument.
2. Env var (`CARDS_RUNNER_TIER_MAP_YAML` /
   `CARDS_RUNNER_TIER_PRICING_YAML`).
3. Ancestor walk from the package looking for the canonical filename.
4. Embedded fallback values (the same ones chunk 3 carried inline).

`strict=True` elevates a missing file to `CanonicalConfigMissing` for
CI smoke checks; the default tolerates a missing file with a single
log line. Malformed YAML degrades to the embedded defaults rather
than crashing the worker.

Side-effect: the canonical `tier_map_claude.yaml` correctly pegs the
opus tier at `claude-opus-4-7`. The chunk-3 stand-in had
`claude-opus-4-6`. One existing test
(`test_model_floor_clamps_the_starting_tier`) was updated to assert
the canonical model id, with a comment recording the chunk-3
divergence the canonical wiring fixed.

### 2. Real `_is_eligible` (dependency-gating + story-drift + pre-approval)

**Module:** `runner/src/cards_runner/daemon/eligibility.py`.

`evaluate_eligibility` is pure: it reads the store and the filesystem
and returns an `EligibilityResult` with `action in {claim, skip, block}`.
The daemon's `_is_eligible` calls it, returns the boolean the poll loop
needs, and routes `block` outcomes through `_route_eligibility_block`
(transitions the card to `blocked` with `merge_status=blocked` and the
reason in the payload).

Three checks, evaluated in this order:

- **Pre-approval.** `requires_pre_approval: true` requires a marker at
  `signals/preapproval/<card_id>.ok`. Per RUNNER_CONTRACT.md
  "Pre-approval gate"; the marker mechanism is one of the choices the
  contract leaves to the runner.
- **Story drift.** When the card carries `story_source_path`, the
  loader re-hashes the file and compares against the card's
  `story_hash`. A mismatch routes the card to `blocked` (the contract's
  "moves the card to `blocked/` ... awaiting re-triage"). An
  unreadable source file is a transient skip, not a block.
- **Dependencies.** Every `depends_on` edge must point to a card in
  `done` with `merge_status: merged`. A done-but-unmerged dep skips
  the claim until the merge lands.

### 3. Tier-aware merge gates

**Module:** `runner/src/cards_runner/daemon/merge_gate.py`.

`MergeGate.apply(claim, record, verified_at)` picks the gate from card
frontmatter:

- `pin_required: true` on the card OR on the canonical tier map for
  the card's `points` -> `human_review`.
- Otherwise: 1-2 -> `auto`, 3-4 -> `sibling_review`, 5-6 ->
  `human_review`.

Outcomes:

- **auto** (and `pr_gate_enabled`) -> `git push -u origin card/<id>`,
  `gh pr create`, `gh pr merge --auto --delete-branch`. Success
  transitions the card to `done` with `merge_status=merged`. Merge
  conflict transitions to `blocked` with `merge_status=conflict`.
- **sibling_review** -> push + open PR; card transitions to `blocked`
  with `merge_status=requires_review` (sibling reviewer is a chunk-5+
  follow-on; chunk 4 leaves the PR open for human or sibling to handle).
- **human_review** -> push + open PR; card transitions to `blocked`
  with `merge_status=open`. Drew (or a sibling) merges externally.

A `pr_gate_enabled=False` daemon (the default, and every chunk-3 test)
short-circuits the gate: the card transitions straight to
`done`/`merged` exactly as chunk 3 did. This keeps the chunk-3 verifier
routing tests passing without GitHub credentials.

**Why `blocked` for awaiting-merge cards.** The contract's status enum
is fixed (`backlog`, `active`, `amendments`, `awaiting_standup_review`,
`done`, `blocked`). The contract describes `blocked` as "cards finished
but unmerged, or paused on a dependency"; an awaiting-merge card is in
the first category. The `merge_status` field carries the nuance the
operator needs to act (`open` vs `requires_review` vs `conflict` vs
`merged`). Chunk 5 will add the unblock side (poll `gh pr view` for
external merges and progress to `done`).

### 4. PR/branch lifecycle via `gh` CLI

**Module:** `runner/src/cards_runner/daemon/pr_lifecycle.py`.

`SubprocessGhRunner` wraps four operations:

- `push(worktree, branch, set_upstream=True)` -- shells `git push -u
  origin <branch>` in the worktree.
- `open_pr(worktree, title, body, base, draft)` -- `gh pr create`;
  parses the PR URL out of stdout into `result.parsed["pr_url"]`.
- `merge_pr(worktree, identifier, strategy="squash")` -- `gh pr merge
  <id> --squash --auto --delete-branch`. Strategy maps to
  `--squash|--merge|--rebase`; unknown values fall back to squash.
- `is_available()` -- `shutil.which("gh") and shutil.which("git")`.

`NullGhRunner` returns `ok=False` for every call. `MergeGate` uses it
when `pr_gate_enabled=False` so an accidental gh call during a chunk-3
test path produces a clear error string instead of a network call.

`parse_pr_view_json` is reserved for chunk 5's poll-for-merged
unblocker (`gh pr view --json state,mergedAt`).

Tests run against `monkeypatch`'d subprocess so the real CLI is never
exercised in CI; an alternate `FakeGhRunner` records and scripts gh
behavior for the merge-gate test suite.

### 5. Forensic-worktree reaper

**Module:** `runner/src/cards_runner/daemon/reaper.py`.

`reap_forensic_run_dirs` walks `_runs/<attempt>/` once per tick. A dir
is reaped when ALL of:

- It is past `cfg.worktree_forensic_ttl_hours` (default 24).
- Its attempt id is not in the daemon's in-memory worker map.
- Either no card claims its attempt_trace_id (orphan forensic dir),
  or the claiming card is in `done` / `blocked`.

A removal failure is logged at `WARNING` and retried next tick. The
reaper never touches a stray file in `_runs/` (only directories), and
non-positive TTLs disable the sweep entirely.

Wired into `Daemon._tick` as step 0 (before worker reap), summary key
`run_dirs_reaped`.

### 6. Boot-time worker-alive check

**Spawner change.** `daemon/spawner.py` now writes
`_runs/<attempt>/worker.pid` after `spawn_in_job` returns. Best-effort;
the alive check tolerates a missing pidfile.

**Daemon change.** `Daemon._boot()` now runs `_boot_alive_check` after
the orphan scan when `cfg.boot_worker_alive_check` is True (the new
default). For each `active` card it reads
`_runs/<attempt_trace_id>/worker.pid`, parses the integer, and calls
`pid_alive(pid)`. A dead pid reclaims the card immediately; a missing
or unparseable pidfile is logged but the card is left to the
heartbeat-orphan path as a safety net.

Wins over the orphan timeout: a daemon that crashes and restarts in
under the `orphan_timeout_minutes` window (default 120) used to wait
the full window before reclaiming a card whose worker the OS killed
along with the daemon. The alive check turns "minutes to hours" into
"single boot tick".

### 7. AC-amendment protocol (executor + runner side)

**Daemon change.** `_post_worker_exit` now harvests the worker's
`status:` field from the projected card file (in addition to the
existing `finished_at`/`actual_tokens`/etc fields) and branches on it:

- `awaiting_amendment_review` (long form) or `amendments` (short form)
  routes to `_route_to_amendments` instead of dispatching the verifier.
- A `change_request:` block in the body without a matching status
  field still routes to amendments, with a `WARNING` log so the
  executor implementation gets fixed.

`_route_to_amendments`:

1. Persists the worker's body (carrying the executor's
   `change_request:` block) into the store via `apply_executor_result`.
2. Transitions the card to `CardStatus.AMENDMENTS` (the canonical
   short form; `awaiting_amendment_review` is the field-value form
   the projector writes back out), clearing the claim provenance.
3. Emits an `amended` event.
4. Drops a `signals/amendments/<card_id>.todo` marker for the human
   review path. Best-effort; the canonical state is the card row.
5. NEVER edits the card's `acceptance_criteria:` block. The runner
   only routes; reviewers (human or sibling) edit AC per the
   contract.

### 8. Live `--invoker sdk-tools` smoke

**Script:** `runner/tests/manual/smoke_sdk_tools.py` (NOT in the
pytest suite; talks to the real Anthropic API).

Builds a tiny card (`bSMK-01-tools`) with a 50-cent cap, a
`points: 1` tier, and an AC block requiring a `file_exists` +
`file_contains` check on `hello.txt`. Runs
`SdkInvoker(use_tools=True).invoke(...)` end-to-end against the live
API.

**Latest run result (recorded 2026-05-20):**

- success: `True`
- model_used: `claude-haiku-4-5-20251001`
- actual_tokens: 4215 (in 3992, out 223) -- prior run 6523
- actual_cost_usd: **$0.0051** (cap $0.5000; ~1% utilization)
- elapsed: 3.3s
- tool calls: 2 (`file_write`, then `report_done`)
- confidence: 0.90 (above the 0.6 threshold; no cascade)
- model calls: 2
- AC verified: `hello.txt` exists with the marker string

The first run produced `success: True / 6523 tokens / $0.0078`; the
second run hit a 30% input-token improvement (probably a cache warm).
Both well under cap. The smoke is repeatable and stable.

## What is verified

`python -m pytest tests/` with `CARDS_DOLT_BIN` set:
**276 passed, 0 skipped** on the Windows host. Without `CARDS_DOLT_BIN`:
**247 passed, 29 skipped** (the Dolt-parametrized half of the chunk
2a store suite). Baseline (chunk 3 on main): 169 passed, 29 skipped.

Chunk 4 adds **78 new SQLite tests** and (now that Dolt is installed
host-wide) unlocks the **29 Dolt tests** the chunk-3 handoff named as
"unverified." Zero regressions.

`ruff check src/ tests/` clean.

New test files:

- `test_canonical_config.py` (13 tests) -- explicit / env / ancestor
  resolution, strict-mode missing, malformed YAML fallback, tier and
  model accessors, out-of-range points clamping.
- `test_eligibility.py` (15 tests) -- five dependency-gating cases,
  four story-drift cases (match, drift, unreadable, no source), three
  pre-approval cases, two evaluation-order cases, frozen dataclass
  guard.
- `test_daemon_eligibility_routing.py` (2 tests) -- daemon-level
  side-effect tests: drifted card transitions to `blocked`, unmet-dep
  card stays in `backlog`.
- `test_merge_gate.py` (17 tests) -- `decide_gate` for every tier
  band + pin override, gate-disabled short-circuit, auto-merge happy
  path, auto-merge conflict -> blocked/conflict, sibling-review path,
  human-review path, pin override on low tier, push failure routing,
  two daemon-level integration tests.
- `test_pr_lifecycle.py` (14 tests) -- gh CLI subprocess wrapper:
  arg construction for push / pr create (with and without `--draft`)
  / pr merge (per strategy), the `is_available` PATH check, file
  not found, timeout, exit-code propagation, PR-view JSON parse
  helper.
- `test_reaper.py` (7 tests) -- old-orphan reap, under-TTL keep,
  in-flight keep, non-terminal-owner keep, terminal-owner reap,
  TTL=0 disable, stray-file skip.
- `test_boot_alive_check.py` (5 tests) -- dead pid reclaims,
  unparseable pidfile reclaims, live pid keeps active, missing
  pidfile defers to heartbeat path, knob-off disables the check.
- `test_amendment_protocol.py` (6 tests) -- long-form status routes
  to amendments, marker file dropped, AC block never edited by the
  runner, short-form status routes too, missing-status + change_request
  body routes with warning, no amendment signal still runs verifier.

## Decisions baked in

**Awaiting-merge cards land in `blocked`, not a new status.** The
contract's status enum is fixed; introducing a new state for
"verifier passed but PR not yet merged" was off-table. The contract's
own `blocked` definition includes "cards finished but unmerged", so
this fits. The `merge_status` field carries the operator-relevant
nuance (`open` vs `requires_review` vs `conflict`).

**`pr_gate_enabled` defaults to `False`.** The chunk-3 verifier
routing tests rely on the "verifier PASS -> done" behavior; turning
the gate on by default would either break those tests or force every
test path to thread a gh fake through. Off-by-default with a daemon
config knob to enable in production keeps both worlds clean.

**`gh pr merge --auto --delete-branch` for the auto-merge path.**
`--auto` lets gh handle the GitHub merge-gate clearing (CI, no
conflicts, approvals); the runner does not have to poll. The card
transitions to `done` immediately because the runner's decision is
made; if the GitHub gate later rejects the merge, gh closes the PR
and a human handles it. The alternative (the runner polls until
merged) would require the chunk-5 unblock side first.

**The merge gate is purely tier-aware on `points` + `pin_required`.**
Per-project relaxation (the contract allows a project to opt into
auto-merging tier-3 cards) is NOT supported in chunk 4; the canonical
`pin_required: true` per the tier map (high stakes always pinned)
locks in the most restrictive interpretation. Project-config
plumbing belongs to a future chunk.

**Story drift reads `story_source_path` from the card frontmatter.**
The contract specifies "if the project config sets
`story_source_path`," but the runner has no project-config plumbing
yet. The simplest path is per-card: the planner stamps
`story_source_path` on cards that have a source file. Chunk 5+ can
add a project-level default if cards routinely omit it.

**`evaluate_eligibility` is pure-function; the daemon writes.** The
unit tests drive eligibility against a SQLite store directly without
the daemon scaffolding, which keeps the test suite fast and the
side-effect surface small. The daemon's `_route_eligibility_block`
is the single place that turns a `block` outcome into a state
transition.

**`worker.pid` is best-effort and not load-bearing.** A missing
pidfile defers to the heartbeat-orphan path; an unparseable pidfile
reclaims. The pid file's content is the source of truth for the
alive check, but the orphan-timeout safety net catches the daemon
that crashed before the spawner ever got a chance to write it.

**The runner accepts both `awaiting_amendment_review` and `amendments`
as the amendment signal.** The long form is the canonical
field-value per the contract; the short form is the subfolder name
some executor implementations may emit. Accepting both costs a line
of code and reduces the chance of an executor-runner mismatch.

**Forensic reaper runs every tick.** The cost of a missed sweep is
unbounded disk growth; the cost of running it every tick is a single
directory walk + a single store query. Cheap, and the per-decision
return shape makes integration debugging trivial.

## What is intentionally NOT in chunk 4

- **Poll-for-merged unblocker.** A card in `blocked/open` or
  `blocked/requires_review` waits for an external merge; the runner
  does not currently progress it to `done` when gh sees the merge.
  `pr_lifecycle.parse_pr_view_json` is the placeholder for that
  feature.
- **Project-config plumbing.** Every per-project knob in chunk 4
  (story_source_path, auto-merge relaxation, sibling reviewer
  identity) is read from the card or the DaemonConfig. A real
  per-project config object (with hot reload) is chunk 5+.
- **Sibling-agent reviewer.** The `sibling_review` gate opens a PR
  and stops. An actual sibling-agent that reads the PR and votes is a
  chunk-5+ feature.
- **`git worktree remove` for terminal cards.** The reaper deletes
  the `_runs/<attempt>/` directory tree; if a card had a real git
  worktree underneath, the git ref still exists. A `git worktree
  prune` sweep is a small chunk-5 add (the run dir is always a child
  of `_runs`, never a tracked worktree path that git knows about, so
  the current path is correct -- but a project that started using
  `git worktree add` directly would want the prune).
- **AC amendment review automation.** The runner drops a marker;
  Drew (or a sibling) edits the AC and toggles the card back to
  `backlog`. Auto-approval / sibling-review of amendments is a
  contract-compliant chunk-5 add.
- **CLI surface for the new knobs.** `pr_gate_enabled` /
  `boot_worker_alive_check` / etc are DaemonConfig fields with sane
  defaults; the CLI does not yet expose flags for them. A chunk-5
  pass adds the flags when the project starts wanting to flip them
  per-invocation.

## Documented gaps

- **Verifier-skip `executor_confidence` is still read from a synthetic
  sidecar slot in tests.** The SDK invoker does not yet write that
  field into `result.json`. Chunk 5 prep should add the write so the
  skip path is exercised end-to-end.
- **The `gh` subprocess wrapper has no live end-to-end test.** Every
  test mocks `subprocess.run` or uses `FakeGhRunner`. A real gh smoke
  (push a throwaway branch, open and close a draft PR) belongs in
  chunk 5 prep alongside the unblocker work.
- **The merge gate writes the PR URL into the lifecycle event payload
  but not onto the card row.** A future schema field (`pr_url` on
  `CardRecord`) would make the URL queryable; chunk 4 keeps the URL
  in the event log only.
- **`mypy --strict` was not run.** Same cause prior handoffs named.
  Code is written to strict standards and `pyproject.toml` keeps
  `strict = true`.

## Chunk 5, in order

1. **Poll-for-merged unblocker.** Daemon-tick sub-task that walks
   `blocked` cards with `merge_status in {open, requires_review}`,
   runs `gh pr view --json state,mergedAt`, and transitions to `done`
   with `merge_status=merged` when GitHub reports the merge landed.
2. **Sibling-agent reviewer.** A reviewer agent (likely a small
   subprocess spawned per `requires_review` card) reads the PR diff
   and the card body and posts a `pr review --approve` or `--comment`.
   Track approvals in a `signals/sibling_reviews/<card_id>.json`
   marker; the unblocker treats an approval as a green light to
   `gh pr merge`.
3. **AC amendment review automation.** Mirror of the sibling reviewer
   for `amendments`-bucket cards: a small reviewer reads the
   `change_request:` block, decides approve / deny, and either edits
   the AC and routes the card back to `backlog`/`active` or writes a
   `change_request_decision:` block and routes to `blocked`.
4. **Project-config plumbing.** A `project.yaml` per project root the
   daemon loads at boot, hot-reloads on a SIGHUP, and threads through
   to the modules that need it (story_source_path defaults, merge
   gate relaxation, sibling reviewer identity).
5. **CLI flags for the chunk 4 knobs.** `--pr-gate`, `--gh`,
   `--auto-merge-strategy`, `--no-boot-alive-check`,
   `--forensic-ttl-hours`. The DaemonConfig fields already exist; the
   CLI parser needs the wiring.
6. **`pr_url` promoted column on `cards`.** Schema migration that
   adds a `pr_url` column, projection writes it, the dashboard reads
   it. Currently the URL lives in the lifecycle event payload only.
7. **`git worktree prune` sweep.** Defensive: a project that started
   using real per-worktree git refs (the runner does not, today)
   would accumulate dead refs without this.

## Environment notes for the next engineer

- All git operations went through PowerShell on the Windows host.
  This worktree (`feature/runner-chunk-4`) is at
  `C:\Users\Drama\Desktop\Claude\agile-cards\romantic-heisenberg-549f16`;
  the main checkout at `C:\dev\agile-cards` is on `main`.
- `anthropic` (0.103.1) is on the host. `dolt` is now installed at
  `C:\Users\Drama\AppData\Local\Programs\Dolt\dolt-windows-amd64\bin\dolt.exe`
  with `CARDS_DOLT_BIN` set at user scope. The dolt binary is NOT on
  PATH; the runner's `resolve_dolt_binary` reads `CARDS_DOLT_BIN`
  directly, so the test suite picks it up via that env var. To run
  the suite with the Dolt half:
  `$env:CARDS_DOLT_BIN = [Environment]::GetEnvironmentVariable("CARDS_DOLT_BIN", "User")`
  before `python -m pytest tests/`.
- `gh` CLI is at `C:\Program Files\GitHub CLI\gh.exe`, version 2.91.0.
  The chunk-4 PR was opened with it; the merge gate's
  `SubprocessGhRunner` shells the same binary.
- `feature/runner-chunk-4` branches off `main` at `9cf53bb` (the
  chunk-3 merge commit). Hold the PR for Drew's review; do NOT
  auto-merge.

## How to run

```powershell
# From a fresh PowerShell session:
cd C:\Users\Drama\Desktop\Claude\agile-cards\romantic-heisenberg-549f16\runner
$env:CARDS_DOLT_BIN = [Environment]::GetEnvironmentVariable("CARDS_DOLT_BIN", "User")
$env:PYTHONPATH = "src"
python -m pytest tests/ -q                  # 276 pass, 0 skip with dolt

# Without dolt:
Remove-Item Env:\CARDS_DOLT_BIN
python -m pytest tests/ -q                  # 247 pass, 29 skip

# Live --invoker sdk-tools smoke (real Anthropic API; ~$0.005 per run):
$env:ANTHROPIC_API_KEY = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
python tests/manual/smoke_sdk_tools.py

# Daemon flags the chunk did NOT yet expose at the CLI -- thread via
# config object construction in a project script until chunk 5 adds
# the flags. Defaults to the chunk-3 behavior (pr_gate_enabled=False)
# so existing callers are unchanged:
cards-runner start --todo-root C:\dev\todo --skip-worktree --invoker sdk-tools
```

End of handoff.

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then the project
`RUNNER_CONTRACT.md` / `README.md`, then this file, then run `vstart`.
