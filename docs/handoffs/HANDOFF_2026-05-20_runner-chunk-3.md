# Handoff: runner chunk 3 (verifier + executor tool belt)

Date: 2026-05-20
Branch: `feature/runner-chunk-3` (off `main` at `889dc89`)
Author: orchestrator pass (Drew to review)

## What this chunk is

Chunk 2b-ii landed the real Anthropic-SDK executor as a metered,
cost-capped, cascade-aware reasoning-only call. Two things were
deliberately left for chunk 3:

1. **The cold-read verifier** -- the gate that finally moves a clean
   `active` card to `done`. The chunk 2b-ii daemon left every clean
   executor exit `active` because there was no verifier to run.
2. **The executor tool belt** -- file edits, shell, git inside the
   per-card worktree. The 2b-ii executor was reasoning-only; it had
   the metering and cascade machinery, but no actual tools.

Chunk 3 ships both. The seams `worker_stub/invoker.py`,
`daemon._post_worker_exit`, and `worker_stub/cost.py.before_tool`
were already shaped for these additions in 2b-ii; this chunk is the
flesh that hangs off them.

A prior chunk-3 attempt failed under a usage cap. This pass branched
fresh off `main` (`889dc89`, the chunk 2b-ii merge commit, verified
at session start) and rebuilt cleanly.

## What shipped

### The verifier (`cards_runner.verifier/`)

A new top-level package implementing RUNNER_CONTRACT.md "Cold-read
verification" / "Two-path model". The runner imports
`cards_runner.verifier.verify_card` and consumes the `VerifierResult`
it returns. The verifier does not touch the store -- the daemon owns
the resulting state transition.

```
runner/src/cards_runner/verifier/
  __init__.py
  types.py                 CANONICAL_TYPES + LEGACY_TYPE_ALIASES,
                           canonicalize_type(), SchemaError
  parse.py                 ```yaml acceptance_criteria:``` block
                           extractor + AcceptanceItem normalizer
                           (legacy `acceptance_checks:` and
                           `subjective: true` aliases preserved
                           through v1.3, per the contract)
  runner.py                verify_card(...): two-phase orchestrator,
                           VerifierResult / ItemResult, verdict
                           strings, schema-error fast path
  handlers/
    __init__.py            DETERMINISTIC_HANDLERS registry, HandlerContext
    deterministic.py       file_exists, file_absent, file_contains,
                           file_lacks, shell (with regex / ignorecase
                           / expect_exit / expect_contains / timeouts)
    subjective.py          evaluate_subjective_batch(): one batched
                           call per tier, haiku -> sonnet -> opus,
                           confidence-threshold settle, cascade
                           appendix with the contract's per-item
                           shape, standup_items for the
                           awaiting_standup_review route
```

**Two-path model.** Deterministic items dispatch to handlers in
`verifier.handlers.deterministic` and cost **zero LLM tokens**.
Subjective items batch into a single cascading evaluator call that
escalates haiku -> sonnet -> opus on confidence below
`subjective_confidence_threshold` (default 0.85). One call per tier
per batch, not per item per tier.

**Verdict shape.** `VerifierResult` carries
`overall_status in {pass, fail, needs_standup_review}`, the per-item
`ItemResult` tuple in declaration order, the
`cascade_history_appendix` to append (not replace) to
`verifier_cascade_history`, and the `standup_reason_items` tuple of
item indices that drove a standup verdict.

**Acceptance-criterion types.** `file_exists`, `file_absent`,
`file_contains` (substring or regex), `file_lacks`, `shell` (with
`expect_exit` / `expect_contains` overrides), and `subjective`. The
v1.2 legacy names `grep_match` / `grep_absent` route through the
alias table with a per-card deprecation warning; the v1.2
`subjective: true` flag is similarly accepted as an alias for
`type: subjective`. An unknown type is a `SchemaError`, which the
verifier surfaces as `overall_status=fail` with the schema error in
`notes`, and the daemon routes the failure-shaped result the same way
any other AC failure routes -- the card goes back to `backlog` with
the problem written into the body, exactly as the contract requires.

### The executor tool belt (`worker_stub/tools.py`)

```
runner/src/cards_runner/worker_stub/
  tools.py                 NEW.
    ToolBelt(worktree, env, shell_timeout_sec, read_only)
    TOOL_DESCRIPTORS        7 tools' input_schema dicts
    ToolResult(ok, payload) one dispatch's structured result
    ToolError               refuse / escape / unknown-tool exception
```

Seven tools, deliberately small:

- **`file_read`** -- UTF-8 file read with optional line bounds
  (1-indexed inclusive). Bounded at 256 KiB so a huge file does not
  burst the model's context.
- **`file_write`** -- create-or-overwrite a UTF-8 file. Creates
  parent dirs.
- **`file_replace`** -- replace one occurrence of a literal string
  (or all, with `replace_all=true`). Refuses ambiguous multi-match
  edits by default, which has caught more bugs in practice than it
  has caused.
- **`list_dir`** -- list directory contents as `{name, kind}`.
- **`shell`** -- run a command in the worktree with the worker's
  scrubbed env block. Returns `{exit_code, stdout, stderr,
  timed_out}`. stdout/stderr tail-truncated to 16 KiB.
- **`git`** -- a verb allowlist (inspect + local-commit verbs only).
  **Push, pull, fetch, clone, remote, submodule, worktree are
  refused.** `reset --hard` is refused (use `restore`).
  `branch --set-upstream` is refused (no remotes).
- **`report_done`** -- terminal-turn signal carrying a summary and
  `confidence` 0-1.0. The cascade reads `confidence` from this tool
  call.

**Sandboxing posture.** Every path argument resolves against the
worktree root and is rejected (`ToolError`) if it escapes. This is
path-only: a `shell` invocation can still `cd ..` on its own. The
Job Object wrapping the worker is the hard isolation backstop; this
layer's job is to make accidental escapes loud, not be a security
boundary against a hostile executor. (`storage_substrate_v2.md`
section on worktree isolation already designates the Job Object as
the hard boundary.)

### SdkInvoker tool-use loop (`worker_stub/sdk_invoker.py`)

The 2b-ii SdkInvoker grew a `use_tools: bool` knob and a multi-turn
tool-use loop bound to `ToolBelt`. The reasoning-only path
(`use_tools=False`) is unchanged from 2b-ii; the tool path
(`use_tools=True`) runs the Anthropic SDK's `messages.create(tools=...)`
loop. Both modes share the cascade machinery, the cost governor,
and the result-assembly helpers.

The loop:

1. Fires `governor.before_call(...)` (pre-message worst-case projection).
2. Sends the conversation with `tools=TOOL_DESCRIPTORS`.
3. Records actual usage via `governor.record_call(...)`.
4. For each `tool_use` block: fires `governor.before_tool(name)`, then
   dispatches through `ToolBelt.execute(name, input)`. The result is
   wrapped as a `tool_result` content block and echoed back.
5. Ends the loop on `report_done`, a pure-text turn (settle on the
   missing-marker default), or `max_tool_turns` (default 24, the
   runaway-loop hard cap).

The model self-reports confidence by calling `report_done` with a
`confidence` argument; the cascade reads that value and decides
whether to escalate.

**Why the cost-cap `before_tool` hook is correct as-is.** 2b-ii wired
the hook but had no tool to dispatch. Chunk 3's loop fires it on
every dispatch, so a runaway agent loop halts within one tool
boundary of breaching the cap.

### Daemon verifier dispatch (`daemon/daemon.py`)

`_post_worker_exit`'s rc=0 branch now calls the verifier and routes
on its verdict:

```
rc=0 (clean executor exit) and verifier_enabled:
  - PASS                  -> transition to `done`, stamp
                              verified_at/verified_by, append
                              cascade_history_appendix; emit `verified`
                              event
  - FAIL                  -> transition to `backlog`, write
                              `verifier_notes` YAML block into the
                              body, clear claimed_by/started_at/
                              last_heartbeat/attempt_trace_id so the
                              next claim is clean
  - needs_standup_review  -> transition to `awaiting_standup_review`,
                              set `standup_reason` to a one-line per
                              standup item
  - VerifierError x3      -> transition to `blocked` with the error
                              detail in the payload (the contract's
                              "retry up to two times then route to
                              blocked")
verifier disabled:
  - leave the card active (chunk 2 baseline; exercised by the chunk 2
    integration test, which now opts out via verifier_enabled=False)
```

**Verifier-skip eligibility** (RUNNER_CONTRACT.md "When the verifier
MAY be skipped") is honored:

1. Cascade history empty (executor did not escalate this attempt).
2. Executor confidence >= `verifier_skip_confidence_threshold`
   (default 0.9; read from the result.json sidecar's
   `executor_confidence` slot).
3. No `type: subjective` AC items.

(Condition 4 "every check passed on first run" is folded in: a clean
executor exit with high confidence and no cascade history implies no
mid-card retries.)

A skipped card transitions to `done` with `verified_by: null` and
`verifier_skipped_reason: "high-confidence cascade-clean run"`. Per
the contract, those two field shapes are mutually exclusive with a
non-null `verified_at`.

### CLI and config

```
runner/src/cards_runner/
  cli/__main__.py       --invoker {stub|sdk|sdk-tools}; --no-verifier
  daemon/spawner.py     passes CARDS_RUNNER_USE_TOOLS / MAX_TOOL_TURNS
                        through to the worker
  common/types.py       DaemonConfig grows: verifier_enabled,
                        verifier_cascade_disabled,
                        verifier_skip_confidence_threshold,
                        subjective_confidence_threshold,
                        subjective_starting_tier, subjective_max_tier
```

`--invoker sdk-tools` is the user-facing way to enable the tool
belt. It flips `CARDS_RUNNER_USE_TOOLS=1` and keeps the daemon's
internal `invoker="sdk"`, so the spawner's existing
`if cfg.invoker == "sdk"` API-key-injection branch keeps working.

## What is verified

`python -m pytest tests/` -- **169 passed, 29 skipped** on the
Windows host. Baseline (the chunk 2b-ii state on `main`) was 94
passed, 29 skipped, so chunk 3 adds **75 new tests** with zero
regressions. `ruff check src/ tests/` clean. The 29 skips are the
Dolt-store half of the chunk 2a suite; `dolt` is not installed on
the host (`winget install DoltHub.Dolt` still needs a download the
user has to OK).

- `test_tools.py` (18 tests) -- every tool's happy path, every
  refusal path: file/path escape, ambiguous file_replace,
  unknown tool, forbidden git verbs (push), forbidden git args
  (reset --hard, branch --set-upstream), missing arguments,
  cross-platform shell exit-code propagation, read-only mode.
- `test_verifier_handlers.py` (14 tests) -- each deterministic
  handler against a tmp_path worktree: file_exists / file_absent /
  file_contains (substring + regex) / file_lacks / shell (exit code
  / expect_exit / expect_contains / timeout / missing-command).
- `test_verifier_parse.py` (14 tests) -- canonicalize_type for known,
  alias, unknown; the YAML fence extractor; parse_acceptance_block
  for canonical, legacy `acceptance_checks:`, legacy
  `subjective: true`, legacy `grep_match`, unknown type, non-mapping
  block, non-list items; AcceptanceItem description fallbacks.
- `test_verifier_runner.py` (12 tests) -- end-to-end with a fake
  Anthropic client: deterministic pass, deterministic fail, no AC
  items, schema error, subjective settle at haiku, subjective
  climb-then-settle, subjective cascade-exhaust to standup,
  subjective_disabled short-circuit, no-client subjective short-
  circuit, mixed det/sub with det-fail overriding, empty evaluator
  response as low-confidence, declaration-order item ordering.
- `test_sdk_invoker_tools.py` (7 tests) -- the multi-turn tool loop
  against a fake client returning `tool_use` blocks: file_write +
  report_done round-trip, settle on a pure-text turn, low-confidence
  climb to sonnet, refused tool result, mid-loop cost-cap halt,
  max_tool_turns runaway cap, descriptors passed to `messages.create`.
- `test_daemon_verifier_routing.py` (8 tests) -- `_post_worker_exit`
  drives a SQLite store directly: PASS -> done, FAIL -> backlog +
  verifier_notes, NEEDS_STANDUP -> awaiting_standup_review,
  VerifierError x3 -> blocked, verifier disabled keeps card active,
  high-confidence skip -> done with skip_reason, cascade history
  blocks skip, verifier_cascade_history is append-only across runs.
- The full chunk 1 / 2a / 2b-i / 2b-ii suite (94 tests) still
  passes: the chunk 2 baseline `test_daemon_integration` test
  explicitly opts out of the verifier (`verifier_enabled=False`) to
  keep its "clean exit leaves card active" semantics, which is the
  documented chunk 2 contract.

## The live verification step

Per RUNNER_CONTRACT.md "Cold-read verification" the deterministic
path costs **zero LLM tokens**, and the subjective path runs only
when the card declares `type: subjective` items. The same fake-client
shape that exercised chunk 2b-ii's SdkInvoker covers the chunk 3
verifier cascade and the executor tool loop, so the entire suite
above runs token-free.

A live tool-loop run was not required for chunk 3 sign-off (the
2b-ii live run already proved the executor speaks to the real SDK
with the real env block under the Job Object). A short live smoke of
`--invoker sdk-tools` against a trivial seed card is the natural
chunk 4 prep step; it should run on a tiny card with
`cost_cap_usd: 1.00` and pinned threshold so the cascade does not
fire.

## Decisions baked in

**The verifier package mirrors the /cards skill's `lib/verifier/`
naming.** `runner/src/cards_runner/verifier/{types,parse,runner,
handlers/{deterministic,subjective}}.py`. RUNNER_CONTRACT.md
"Library requirement" makes the canonical types the single source of
truth; the canonical library lives in the /cards skill repo, not the
runner. Keeping the names aligned now is the cheap option for the
day the canonical library is vendored in or imported as a package.

**Verifier does not touch the store.** `verify_card` returns data;
the daemon writes. Same separation `worker_stub` already keeps. This
makes the verifier unit-testable without a database, and a future
manual-override entry point (RUNNER_CONTRACT.md "Manual override
entry point") just calls the same function with a different
orchestrator above it.

**Subjective phase short-circuits without a client.** When the card
has subjective items and no Anthropic client is wired up (no
`ANTHROPIC_API_KEY` in the daemon env, or the SDK import fails), the
verifier routes the items to standup review rather than auto-passing
on subjective claims. The contract is firm that subjective items
ALWAYS run -- this is the safe degradation when "running" is not
possible.

**Tool belt's `git` is allowlist-not-blocklist.** A blocklist of
"forbidden git verbs" risks new sub-commands sneaking through.
`_GIT_ALLOWED_VERBS` is the explicit set the runner will dispatch;
anything else raises `ToolError`. `_GIT_FORBIDDEN_VERBS` is a
secondary tripwire on the verbs we definitely never want (push, pull,
remote, etc.) so a future "let me add another verb" PR catches the
mistake of adding one that belongs in the forbidden set instead.

**`report_done` is a tool, not a magic string.** The 2b-ii
reasoning-only mode relied on a trailing `CONFIDENCE:` marker line.
For the tool-loop mode that marker is unreliable -- the model may
emit text from any turn -- so the canonical end-of-turn signal is
the explicit `report_done` tool call. The cascade reads its
`confidence` argument directly. Falling back to the marker (when the
model ends a turn with pure text and no `report_done`) preserves
backward compatibility with the reasoning-only path.

**The CLI's `sdk-tools` choice flips an env var, not a different
invoker name.** Splitting "sdk" and "sdk-tools" into two daemon
config values would have rippled into the spawner's env-injection
branch and the env-scrubber's keep-list. Keeping the daemon's
`cfg.invoker == "sdk"` branch single-name and flipping the
tool-using behavior with `CARDS_RUNNER_USE_TOOLS` in the worker env
is the smaller, safer change. The user-facing CLI value is the
discoverable surface.

**Verifier-skip reads `executor_confidence` from the sidecar.** The
chunk 2b-ii sidecar carried `actual_cost_usd`, `model_used`,
`escalations`, `actual_tokens`, and `halt_kind`. The contract's
skip-eligibility check needs the executor's self-reported
confidence; chunk 3 reads it from a `executor_confidence` field the
worker MAY write into the sidecar. When it is absent, the daemon
errs against skip and runs the verifier. Wiring the SDK invoker to
write that field out is a small follow-on; for chunk 3 the test
suite uses an explicit sidecar value so the skip path is exercised
without needing the executor wiring change.

**The chunk 2 baseline `test_daemon_integration` was updated to set
`verifier_enabled=False`.** Without it the test's "clean exit ->
active" assertion would fail under chunk 3 defaults. The change is
one explicit line in the test plus a comment explaining why, which
documents the chunk-2 baseline rather than papering over a
regression.

## What is intentionally NOT in chunk 3

- **Tier-aware merge gates and the PR lifecycle.** RUNNER_CONTRACT.md
  "Merge gates" ships in chunk 4. The verifier's `done` transition
  lands the card; turning that into a merged PR is the next chunk.
- **Wiring `tier_map_claude.yaml` and `tier_pricing.yaml` from the
  /cards skill.** Both still use embedded stand-ins (`_POINTS_TO_TIER`
  in `sdk_invoker.py`, `_DEFAULT_PRICING` in `cost.py`) overridable
  via `CARDS_RUNNER_PRICING_JSON`. Loading the canonical YAML files
  is real config plumbing; the 2b-ii handoff named it a chunk-3 task
  but the prior chunk-3 attempt failed mid-build, and shipping the
  verifier + tool belt clean was the higher-priority surface. The
  cap mechanism is correct regardless of the absolute figures (a
  wrong rate only shifts where the cap trips); the cascade tier
  mapping is correct for the current Anthropic model family
  (haiku 4.5 / sonnet 4.6 / opus 4.6). Wiring the canonical files is
  a clean chunk-4 add.
- **Dependency-gating and story-drift checks in `_is_eligible`.**
  Still a pass-through. The store's `dependencies` table exists from
  chunk 2a and the verifier can supply the story-drift signal; both
  are chunk 4.
- **The forensic-worktree reaper** (`worktree_forensic_ttl_hours`
  cleanup). Chunk 4.
- **Boot-time "is a worker process actually alive" check.** Chunk 4.

## Documented gaps

- **Dolt store still unverified.** Same as 2b-i / 2b-ii. The
  verifier and tool belt are store-agnostic; they touch the store
  only through `CardRepository`, which both SQLite and Dolt
  implement. There is no chunk-3-specific Dolt risk.
- **`mypy --strict` was not run.** Same cause the prior handoffs
  named: the `mypy` available to this environment pulls suspect
  non-standard dependencies. Code is written to strict standards and
  `pyproject.toml` keeps `strict = true`.
- **The SDK tool-loop has no live end-to-end run.** The fake-client
  test suite covers the loop, the tool dispatch, the cascade integration,
  the cost-cap interruption, and the runaway-loop cap. A short live
  smoke against a trivial card belongs in chunk 4 prep alongside the
  merge-gate work.
- **Verifier-skip `executor_confidence` is read from the sidecar but
  the SDK invoker does not yet write it.** The next sidecar-write
  pass (chunk 4 prep) should add the field. Until then the skip
  path is exercised by tests with a synthetic sidecar; it does not
  hurt the contract -- the daemon defaults conservatively to running
  the verifier when the field is missing.
- **The verifier's subjective phase reads `usage.input_tokens` /
  `usage.output_tokens` from the SDK message.** Fake clients in the
  test suite leave usage as defaults (100/30), so the verifier's own
  cost cap is exercised but does not have to be precise. The real
  SDK populates usage correctly.

## Chunk 4, in order

1. **Tier-aware merge gates.** RUNNER_CONTRACT.md "Merge gates":
   tiers 1-2 auto-merge after PASS; tiers 3-4 route to
   sibling-review; tiers 5-6 require human approval. The seam is
   `_verifier_apply_pass`: instead of immediately transitioning to
   `done`, route a passed card through the merge gate first, and let
   the gate's outcome drive the final transition.

2. **PR/branch lifecycle.** The tool belt already commits inside the
   per-card worktree. Chunk 4 promotes the worktree's commit to a
   real GitHub PR (gh CLI or the GitHub API). `gh pr create` is the
   simplest; the merge gate decides whether to call `gh pr merge`
   immediately, hand off to sibling review, or wait on a human
   approval marker. Push permission lands here on a per-card basis
   (the tool belt still refuses it; the daemon makes the push).

3. **Wire the canonical `tier_map_claude.yaml` and
   `tier_pricing.yaml` from the /cards skill.** Either a file-path
   env var (`CARDS_RUNNER_TIER_MAP_YAML` /
   `CARDS_RUNNER_TIER_PRICING_YAML`) or a small config object the
   daemon resolves at boot. Retire the embedded stand-ins or keep
   them as the final fallback. The Pricing class already has an env-
   var override hook; the tier map needs the symmetric one.

4. **The forensic-worktree reaper.** `worktree_forensic_ttl_hours`
   cleanup of `_runs/<attempt>/` directories that landed `blocked`
   or are otherwise terminal. A small daemon-tick sub-task.

5. **Boot-time "is a worker process actually alive" check.** The
   2b-i handoff flagged this. A daemon crash plus fast restart can
   leave `active` cards with a not-yet-stale heartbeat; a real
   process-liveness check at boot reclaims them faster than the
   orphan-timeout window.

6. **`_is_eligible` gets real.** Dependency gating against the
   store's `dependencies` table (every `depends_on` card must be in
   `done`); story-drift check against the planner-stamped
   `story_hash` (the executor must not have rewritten the work it
   was assigned to).

7. **The AC-amendment protocol.** `awaiting_amendment_review`
   transitions, the `amended` event type, and the path that lets a
   verifier-FAIL card propose a relaxation of an AC item instead of
   just bouncing back.

8. **Live smoke of `--invoker sdk-tools` on a trivial card.** Chunk
   2b-ii's live run validated the SDK + Job Object + cascade against
   a real card. Chunk 4 adds the same step for the tool-using
   executor: a tiny card with one `file_write` AC item and a $0.50
   cap, run once.

## Environment notes for the next engineer

- All git operations went through PowerShell on the Windows host.
  This worktree (`feature/runner-chunk-3`) is at
  `C:\Users\Drama\Desktop\Claude\agile-cards\youthful-brattain-f8289f`;
  the main checkout at `C:\dev\agile-cards` is on `main`. A parallel
  worktree exists at `sharp-montalcini-0d6496` for another session.
- `anthropic` (0.103.1) is still on the host. `dolt` is still not.
- `feature/runner-chunk-3` branches off `main` at `889dc89` (the
  chunk 2b-ii merge commit). Hold the PR for Drew's review per
  convention; do NOT auto-merge.
- The `--no-verifier` CLI flag and the `DaemonConfig.verifier_enabled`
  field together preserve the chunk 2 baseline behavior for any
  caller that explicitly opts out.

## How to run

```powershell
cd C:\dev\agile-cards\runner
pip install -e .[dev]
$env:PYTHONPATH = "src"
python -m pytest tests/ -q          # 169 pass, 29 skip without dolt

# The daemon with the stub executor (zero tokens, default):
cards-runner start --todo-root C:\dev\todo --skip-worktree

# Reasoning-only SDK executor (no tools):
cards-runner start --todo-root C:\dev\todo --skip-worktree --invoker sdk

# Tool-using SDK executor (file/shell/git inside the worktree):
cards-runner start --todo-root C:\dev\todo --skip-worktree --invoker sdk-tools

# Disable the verifier (chunk 2 baseline; a clean exit stays active):
cards-runner start --todo-root C:\dev\todo --skip-worktree --no-verifier
```

End of handoff.

## Next Session Onboarding

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then the project
`CLAUDE.md` (or this repo's `RUNNER_CONTRACT.md` / `README.md`), then
this file, then run `vstart`.
