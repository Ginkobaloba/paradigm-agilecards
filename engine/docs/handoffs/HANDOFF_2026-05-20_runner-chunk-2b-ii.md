# Handoff: runner chunk 2b-ii (the real executor)

Date: 2026-05-20
Branch: `feature/runner-chunk-2b-ii` (off `main`)
Author: orchestrator pass (Drew available for the live run)

## What this chunk is

Chunk 2b-i shipped the canonical cutover: claiming a card is a
transactional database `UPDATE`, and the worker is driven through a
projected card file. The one thing still fake was the worker itself
-- it was the chunk 1 `StubInvoker` (sleep, return a canned
completion). Chunk 2b-ii replaces the stub with the real thing.

Per the 2b-i handoff's ordered "Chunk 2b-ii" plan, items 2-6:

2. `SdkInvoker` -- the real Anthropic-SDK-in-process executor.
3. The `_winapi.CreateProcess` Job Object refinement.
4. Cost-cap hooks.
5. The executor cascade.
6. Exit-code routing in `daemon._post_worker_exit`.

Item 1 (install Dolt, re-run the Dolt store suite) was explicitly
out of scope for this pass -- see "Documented gaps".

## What shipped

**`SdkInvoker` -- the real executor (`worker_stub/sdk_invoker.py`).**
A new `Invoker` implementation at the existing seam. It opens an
`anthropic.Anthropic` client in-process and runs the card through a
real model call. The daemon, the spawner, and `run_worker` did not
need to change shape for it -- which is exactly what the `Invoker`
seam was built for. `worker_stub/worker.py`'s `main_from_env` picks
stub vs SDK from the `CARDS_RUNNER_INVOKER` env var; the daemon's
`DaemonConfig.invoker` and the CLI's `--invoker {stub,sdk}` flow that
through.

The 2b-ii executor is **reasoning-only**. It reads the card and
produces a structured completion report (what the work entails, how
it would be implemented, risks, a per-AC assessment). It does not yet
hold a tool belt -- file edits, shell, git. That decision and its
reasoning are below; the short version is that tool-equipped
execution intersects the verifier and the merge gates and belongs
with chunk 3. What 2b-ii proves is the real machinery: a metered,
cost-capped, cascade-aware model call driving a card end to end.

**Cost-cap enforcement (`worker_stub/cost.py`).** `CostMeter`,
`Pricing`, `CostGovernor`, `CostCapExceeded`. The governor fires
three hooks around every model call: `before_call` (pre-message),
`before_tool` (pre-tool-use), and `record_call` (post-call). The
base `anthropic` Messages SDK has no native hook registry the way the
Agent SDK does, so the "SDK hooks" the runner design calls for are
implemented as explicit callbacks the `SdkInvoker` fires. The
enforcement point is the one RUNNER_CONTRACT.md names -- each
model-call boundary -- and the check is sub-millisecond, so a runaway
agent loop is halted within one turn of breaching. `before_call`
projects the worst-case cost of the upcoming call and refuses it if
the projection would exceed the cap; this is also the
"re-evaluate the cap immediately after each escalation" check the
cascade section requires. A breach raises `CostCapExceeded`, the
invoker returns `halt_kind="cost_cap"`, the worker exits
`EXIT_COST_CAP_HALT` (11), and the daemon routes the card to
`blocked`. USD is always derived from tokens, never stored on a card.

**The executor cascade.** RUNNER_CONTRACT.md "Cascade-on-confidence
routing", made real. After each step the executor self-reports a
confidence (a `CONFIDENCE: <n>` trailer line, parsed out). Below the
threshold (`cascade_escalation_threshold`, default 0.6) the invoker
escalates one tier and re-runs the step, capped at **2 escalations**
(the contract's hard cap, clamped in `__post_init__`). Each
escalation appends a `cascade_history` entry of the contract shape
`{from_tier, to_tier, reason, confidence_at_escalation, at}` plus the
forensic keys `from_model`, `to_model`, `attempt_trace_id`. If the
cascade exhausts its two climbs still below threshold, the run halts
`cascade_exhausted`, the worker exits `EXIT_HALT_SIGNAL` (12), and
the daemon routes the card to `blocked` -- the contract's "a verifier
that cannot reach a verdict surfaces the question", applied to the
executor side.

**The `_winapi.CreateProcess` Job Object refinement
(`common/process_group.py`).** Chunk 1 created the worker with
`subprocess.Popen` and called `AssignProcessToJobObject` immediately
after -- a microsecond race where a descendant spawned between
`CreateProcess` and the assignment escapes the job. `subprocess.Popen`
exposes no main-thread handle, so it could not create-suspended-then-
resume; chunk 1 named the fix and deferred it. `_spawn_win32` now
drops to `_winapi.CreateProcess` directly: the worker is created
`CREATE_SUSPENDED`, assigned to the job while frozen, then resumed via
`ResumeThread`. No descendant runs before the job owns the process.
`_Win32Process` is a `subprocess.Popen`-compatible facade over the
raw process handle. This refinement matters now precisely because the
real executor imports the SDK, which spins up HTTPS connection
workers.

**Exit-code routing (`daemon._post_worker_exit`).** Chunk 2b-i left
every worker exit `active`. 2b-ii routes on the code: `0` clean is
left `active` (the verifier owns the `done` arrow, chunk 3); `11` /
`12` halts transition the card to `blocked` with the halt detail in
the `blocked` event; any other non-zero is left `active` for orphan
reclaim, exactly as chunk 1. Every escalation the executor recorded
this attempt is also emitted as an `escalated` event in
`card_events`, filtered by `attempt_trace_id` so a re-claimed card
does not re-emit its earlier escalations. The worker writes a
`result.json` sidecar into the run dir; the daemon reads it to
enrich the `executed` event payload with token / cost / cascade
detail (without putting derived USD on the card).

```
runner/src/cards_runner/
  worker_stub/
    cost.py         NEW  CostMeter / Pricing / CostGovernor / CostCapExceeded
    sdk_invoker.py  NEW  SdkInvoker -- the real executor + cascade
    invoker.py      InvokeResult gains halt_kind / cascade_history / cost
    worker.py       invoker selection; exit-code mapping; result.json sidecar
  common/
    process_group.py  _winapi.CreateProcess suspended-spawn; _Win32Process
    types.py          WORKER_RESULT_NAME; DaemonConfig.invoker
  daemon/
    spawner.py        injects ANTHROPIC_API_KEY + executor knobs in sdk mode
    daemon.py         _post_worker_exit: exit-code routing + escalated events
    __main__.py       --invoker
  cli/__main__.py     --invoker (start)
runner/tests/
  test_cost_governor.py  NEW  12 tests
  test_sdk_invoker.py    NEW  10 tests (fake client, zero tokens)
  test_exit_routing.py   NEW  6 tests
  test_process_group.py  NEW  4 tests (spawn / exit / stdout / kill-tree)
runner/pyproject.toml   anthropic>=0.40 declared
```

## What is verified

`python -m pytest tests/` -- **94 passed, 29 skipped** (baseline was
62 / 29; +32 new tests). `ruff check` clean. Every changed file
`py_compile`s and imports.

- `test_cost_governor.py` -- pricing math, the meter's accumulation,
  and all three governor hooks: `before_call` raising on a projection
  overrun and on already-over-budget, `record_call` raising on a
  post-call breach, `before_tool` raising only when over,
  `cap=None` never raising.
- `test_sdk_invoker.py` -- the SdkInvoker against a fake Anthropic
  client (zero tokens): a high-confidence single turn settles; low
  confidence climbs haiku->sonnet and exhausts at 2 escalations; a
  mid-cascade settle; a cost cap halting before the first call and
  after a call overruns; no-cap never halting; a missing confidence
  marker not forcing escalation; an SDK exception becoming a failed
  result; `model_floor` clamping the start tier; the escalation cap
  clamped to 2.
- `test_exit_routing.py` -- `_post_worker_exit` drives a SQLite store
  directly: a clean exit leaves the card `active`; cost-cap and
  cascade-exhausted halts route to `blocked`; a plain error leaves it
  `active`; `escalated` events are emitted only for the current
  attempt; the `executed` payload carries the sidecar detail.
- `test_process_group.py` -- `spawn_in_job` end to end: exit codes
  propagate, stdout is captured to a file, `kill_tree` terminates a
  running child. On the Windows host this exercises the
  `CREATE_SUSPENDED` -> assign -> resume path and `_Win32Process`.
- `test_daemon_integration.py` (unchanged) still passes on the
  Windows host -- which means the new `_spawn_win32` spawns, reaps,
  and lands stub-worker results through the full daemon loop.
- The chunk 2a / 2b-i store and daemon suites did not regress.

## The live verification run

Building everything token-free was the protocol, and it held: the
entire suite above runs with **zero** real API calls. The single
minimal live run -- one trivial synthetic card on Haiku -- then
proved the executor works against the real SDK.

**Status: PASSED (2026-05-20).** A trivial smoke card (`points: 1`,
`cost_cap_usd: 0.50`) was seeded into a fresh SQLite store and run
through the full daemon loop in `--invoker sdk` mode, with
`CARDS_RUNNER_CASCADE_THRESHOLD=0.0` (pins it to exactly one model
call) and `CARDS_RUNNER_MAX_OUTPUT_TOKENS=512`. Result:

- The daemon claimed the card, projected it, spawned the worker under
  the Job Object via the new `_winapi.CreateProcess` suspended-spawn
  path -- with the real `anthropic` SDK loaded in that worker, which
  is exactly the case the refinement was built for -- and reaped it.
- `SdkInvoker` made **one** Haiku call (`claude-haiku-4-5-20251001`):
  **627 tokens** (355 in, 272 out), **derived cost $0.001715**
  against the $0.50 cap. Confidence self-reported 0.99; 0 escalations.
- The card ended `active` (a clean executor finish is not the `done`
  arrow -- the verifier owns that, chunk 3), with the SdkInvoker
  completion report and run-metadata footer in its stored body.
- Events: `drafted` -> `claimed` -> `executed`. The `executed`
  payload carried `actual_cost_usd: 0.001715`, `model_used`,
  `escalations: 0`, `exit_code: 0`, `ok: true`. No `escalated`
  events, as expected.

**Total real token spend for chunk 2b-ii verification: 627 tokens
(~$0.0017), one Haiku call.** Everything else is token-free.

One correction to note for the next engineer: a live run needs a
card *seeded* into the store first -- the daemon idles against an
empty store, and there is no `cards-runner add-card` CLI (cards come
from the `/cards` skill). The run above used a small throwaway
seed-and-run driver, not committed. A `cards-runner seed` or similar
dev affordance would be a reasonable small chunk-3 addition.

## Decisions baked in

**`anthropic` (the Messages SDK), not the Agent SDK.** The brief and
the 2b-i handoff both name "the Anthropic Python SDK (`anthropic`
package)". The Agent SDK has a native hook registry; the Messages SDK
does not. So the cost-cap "hooks" are implemented as explicit pre/post
callbacks the invoker fires around each `messages.create`. The
enforcement point -- each model-call boundary -- is exactly what
RUNNER_CONTRACT.md's "Cost cap enforcement" specifies, so this is a
faithful implementation, not a workaround. Mid-stream interruption of
a single pathologically long call is not done; the Job Object
resource limits and the daemon's wall-clock `force_kill_after_seconds`
are the backstops for that case, which is the layering the design
already describes.

**The 2b-ii executor is reasoning-only.** A tool-equipped coding
agent (file edits, shell, git) is a large surface that intersects the
verifier, the merge gates, and worktree sandboxing -- all chunk 3-4
territory. Shipping an unsandboxed tool executor inside 2b-ii would
couple two hard problems. The cost-cap `before_tool` hook is wired
and unit-tested anyway, so a future tool loop inherits enforcement
for free. 2b-ii proves the executor *machinery* (metering, the cap,
the cascade, exit routing) live; the tool belt is a clean follow-on.

**Points-driven model resolution via an embedded map.**
RUNNER_CONTRACT.md keys the executor's planned tier on `card.points`
through `tier_map_claude.yaml`. That file lives in the /cards skill,
not the runner repo. `sdk_invoker._POINTS_TO_TIER` (1-2 haiku, 3-4
sonnet, 5-6 opus) is the runner-side stand-in; the card's `model`
field is advisory. Wiring the canonical file is a chunk 3 task.

**Pricing is an embedded estimate table.** Same story --
`tier_pricing.yaml` is a /cards artifact. `cost._DEFAULT_PRICING`
carries published-rate estimates (haiku $1/$5, sonnet $3/$15, opus
$15/$75 per Mtok), overridable via `CARDS_RUNNER_PRICING_JSON`. The
cap *mechanism* is correct regardless of the absolute figures -- a
wrong rate only shifts where the cap trips. Wiring the canonical
pricing file is a chunk 3 task.

**`cascade_history` entries carry an `attempt_trace_id`.** The
contract shape is `{from_tier, to_tier, reason,
confidence_at_escalation, at}`. The runner adds `attempt_trace_id`
(and `from_model` / `to_model`) as forensic keys. The daemon needs
`attempt_trace_id` to emit `escalated` events idempotently: the
history is append-only across re-claims, so the daemon filters to the
current attempt's entries. Extra keys are additive and harmless.

**A `result.json` sidecar, not card fields, carries cost detail.**
RUNNER_CONTRACT.md is firm that cards do not store USD. The worker
writes token / cost / cascade detail to `_runs/<attempt>/result.json`;
the daemon reads it to enrich the `executed` event payload. Card
state stays contract-clean.

**The `worker_stub` package keeps its name.** It is now a misnomer --
it holds the real executor too -- but renaming it ripples into the
spawner's `python -m cards_runner.worker_stub`, the pyproject script
entry, and every test import, for zero behavior gain. The 2b-i
handoff explicitly said "the daemon and `run_worker` do not change".
A rename to `worker/` is cosmetic debt noted for a future cleanup.

**The `_winapi` approach over thread-enumeration.** The other way to
get `CREATE_SUSPENDED` with a resumable thread is to spawn via
`subprocess.Popen` and enumerate the process's threads to find the
main one. `_winapi.CreateProcess` hands back the thread handle
directly -- cleaner, and the approach the 2b-i handoff prescribed.
This build confirmed `_winapi` exposes no `Handle` type on this
CPython, so `_Win32Process` manages the raw int handle and closes it
on GC.

## What is intentionally NOT in chunk 2b-ii

- Tool-equipped execution (the executor's file/shell/git tool belt).
  Chunk 3+.
- The verifier and the `done` transition. A clean executor finish is
  still left `active`; chunk 3's verifier moves it.
- The canonical `tier_map_claude.yaml` and `tier_pricing.yaml`
  wiring. Embedded stand-ins for now.
- Dependency gating, story-drift, pre-approval at claim time.
  `_is_eligible` is still a pass-through. Chunks 3-4.
- Streaming / mid-call cost interruption. The cap is enforced at
  call boundaries; the Job Object and wall-clock kill are the
  backstops for one runaway call.

## Documented gaps

- **The live run is done** (see "The live verification run" --
  PASSED, 627 tokens / ~$0.0017). No gap remains here; kept in the
  list only as the pointer.
- **The Dolt store is still not re-verified.** Per the brief, Dolt
  was not installed this pass (`winget install DoltHub.Dolt` needs a
  download only the user can do). The 29 Dolt tests still skip. The
  executor is store-agnostic -- it never touches the store; the
  daemon does, through the same `CardRepository` interface SQLite and
  Dolt share -- so there is no executor-specific Dolt risk. Installing
  Dolt and re-running `pytest tests/store` remains a deploy step.
- **`mypy --strict` was not run.** Same cause the 2b-i handoff named:
  the `mypy` available to this environment pulls suspect non-standard
  dependencies. The new code is written to strict standards and
  `pyproject.toml` keeps `strict = true`. The `anthropic` package
  ships `py.typed`, so no new mypy override was needed.
- **The cascade fires only on an explicit low self-report.** If the
  model omits the `CONFIDENCE:` trailer the run settles (a missing
  marker defaults high, so a formatting slip does not burn tokens on
  a needless climb). A project that wants a stricter probe -- test
  pass counts, lint status -- would layer it per RUNNER_CONTRACT.md's
  "the runner combines them"; that is a chunk 3 project-config task.
- **Pricing figures are estimates.** See the decision above.

## Chunk 3 and chunk 4, as they look from here

**Chunk 3 -- the verifier and the tool belt.** Two coupled pieces.
The verifier (`verifier.runner.verify_card`, the two-path
deterministic + subjective-cascade model in RUNNER_CONTRACT.md's
"Cold-read verification") is what finally moves a clean `active` card
to `done`. `_post_worker_exit`'s rc=0 branch is the seam it plugs
into. The executor tool belt -- file edits, shell, git, run under the
worktree sandbox -- is the other piece; the cost-cap `before_tool`
hook and the multi-turn shape of the SdkInvoker loop are already
built for it. Wiring `tier_map_claude.yaml` and `tier_pricing.yaml`
from the /cards skill belongs here too, retiring the embedded
stand-ins. `_is_eligible` grows real dependency-gating and the
story-drift check against the `dependencies` table.

**Chunk 4 -- merge orchestration and the reaper.** The tier-aware
merge gates (auto-merge for tiers 1-2, sibling-review for 3-4, human
for 5-6), the PR/branch lifecycle, the AC-amendment surfacing
(`awaiting_amendment_review`), and the forensic-worktree reaper
(`worktree_forensic_ttl_hours`). The boot-time "is a worker process
actually alive" check the 2b-i handoff flagged also fits here.

## Environment notes for the next engineer

- All git operations went through PowerShell on the Windows host. The
  Linux sandbox mount is no-unlink and serves stale cache; this pass
  built and tested entirely on Windows-native tools, as 2b-i did.
- `anthropic` (0.103.1) is installed on the host. `dolt` is not.
- `feature/runner-chunk-2b-ii` branches off `main` (which now has
  chunks 1, 2a, 2b-i merged). It is a single non-stacked PR.
- A pre-existing untracked `docs/audits/` directory is on the host;
  it is not part of this chunk and was not committed.

## How to run

```powershell
cd C:\dev\agile-cards\runner
pip install -e .[dev]            # now also pulls `anthropic`
$env:PYTHONPATH = "src"
python -m pytest tests/ -q       # 94 pass, 29 skip without dolt

# the daemon with the stub executor (zero tokens, the default):
cards-runner start --todo-root C:\dev\todo --skip-worktree

# the daemon with the real SDK executor (needs ANTHROPIC_API_KEY):
cards-runner start --todo-root C:\dev\todo --skip-worktree --invoker sdk
```

End of handoff.
