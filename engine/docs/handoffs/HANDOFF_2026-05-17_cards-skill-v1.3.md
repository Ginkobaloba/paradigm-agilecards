# HANDOFF: cards skill v1.3 (verifier-as-structured-runner refactor)

Date: 2026-05-17 (work landed 2026-05-18 in the implementation
session)
Branch: `feature/cards-v1.3-verifier-refactor`
PR target: `Ginkobaloba/agile-cards` main
Predecessor handoff:
[`HANDOFF_2026-05-17_cards-skill-v1.2.md`](./HANDOFF_2026-05-17_cards-skill-v1.2.md)

Future sessions: read `C:\dev\SESSION_PROTOCOL.md`, then this file,
then the design doc at
[`docs/design/v1.3_verifier_refactor.md`](../design/v1.3_verifier_refactor.md).
The design doc carries the load-bearing reasoning and the locked
answers to the nine open questions; this handoff documents what
shipped, what remains, and the token-cost validation plan.

---

## What shipped

The v1.3 verifier-as-structured-runner refactor. Four logical
chunks, committed separately:

1. `lib/verifier/` reference implementation. 24 files, roughly 3.9k
   lines including tests. Per-type handlers, schema validator,
   project config view, cascade-aware orchestrator. Imports cleanly
   under Python 3.11; tests pass under `python -m unittest discover`
   on Windows (52 tests, exit code 0).

2. `SKILL.md`, `RUNNER_CONTRACT.md`, `README.md`,
   `templates/card.md`, `templates/project_config.yaml`, and
   `examples/b001-03-add-rate-limit-middleware.md` updates to
   reflect the v1.3 schema and the cascade behavior.

3. `docs/design/v1.3_verifier_refactor.md` committed into the main
   history alongside the implementation.

4. This handoff.

### Concrete behavior changes

- Verifier dispatch now happens via `verifier.runner.verify_card`
  rather than as a single Claude reasoning agent call. Deterministic
  AC items spend zero LLM tokens. Subjective items batch into a
  cascading evaluator call.

- Subjective items return strict pass/fail plus confidence. When
  confidence falls below `subjective_confidence_threshold` (default
  0.85), the orchestrator escalates haiku -> sonnet -> opus. The
  cap is `subjective_max_tier` (default opus).

- Cards whose subjective cascade exhausts without reaching the
  threshold move to `awaiting_standup_review/` rather than
  auto-passing or auto-failing. Drew's directive captured in the
  locked answer 6.

- Every subjective evaluation appends one entry per attempt to
  `verifier_cascade_history` on the card. Append-only, never reset.

- `verifier_schema_version: "1.3"` is required on every card the
  v1.3 planner writes.

- v1.2 type names (`shell`, `grep_match`, `grep_absent`) and the
  v1.1 per-item `subjective: true` flag are accepted as deprecated
  aliases. Hard removal in v1.4 along with a
  `/cards migrate-ac-schema` rewrite tool.

### Files touched (summary)

```
docs/design/v1.3_verifier_refactor.md        new (561 lines)
docs/handoffs/HANDOFF_2026-05-17_cards-skill-v1.3.md   new (this file)
lib/verifier/__init__.py                     new
lib/verifier/types.py                        new (canonical type registry)
lib/verifier/schema.py                       new (validator)
lib/verifier/project_config.py               new
lib/verifier/result.py                       new
lib/verifier/runner.py                       new (orchestrator)
lib/verifier/handlers/__init__.py            new
lib/verifier/handlers/file_exists.py         new
lib/verifier/handlers/file_absent.py         new
lib/verifier/handlers/file_contains.py       new
lib/verifier/handlers/file_absent_content.py new
lib/verifier/handlers/command.py             new (with security commentary)
lib/verifier/handlers/python_assert.py       new (AST-inspection sandbox)
lib/verifier/handlers/http_status.py         new
lib/verifier/handlers/http_contains.py       new
lib/verifier/handlers/subjective.py          new (cascade evaluator)
lib/verifier/tests/__init__.py               new
lib/verifier/tests/test_schema.py            new
lib/verifier/tests/test_file_handlers.py     new
lib/verifier/tests/test_command.py           new
lib/verifier/tests/test_python_assert.py     new
lib/verifier/tests/test_http_handlers.py     new
lib/verifier/tests/test_subjective_cascade.py new
lib/verifier/tests/test_end_to_end.py        new
SKILL.md                                     modified
RUNNER_CONTRACT.md                           modified
README.md                                    modified
templates/card.md                            modified
templates/project_config.yaml                modified
examples/b001-03-add-rate-limit-middleware.md modified
```

### Test posture

- 52 unit + integration tests, all passing on Windows under
  `PYTHONPATH=$PWD/lib python -m unittest discover -s lib/verifier/tests`.
- The HTTP suite binds to `127.0.0.1` via `http.server`, so the
  tests do not depend on the wider internet.
- The subjective cascade tests inject a scripted mock evaluator;
  the production Anthropic SDK is never called from the test suite.
- The `python_assert` suite exercises the AST-rejection paths
  explicitly: `__import__`, `os.system`, write-mode `open`, dynamic
  `open` mode, and syntactic garbage all produce a `passed=False`
  HandlerResult with a clear evidence message rather than raising.
- The `command` suite covers env scrubbing (a synthetic
  `FAKE_API_KEY` set in the parent env is verifiably NOT visible
  to the child), per-item env overrides, timeout enforcement,
  shlex-based string splitting, and per-item cwd overrides.

### Security posture (command handler)

Captured verbatim in the module-level docstring of
`lib/verifier/handlers/command.py`, sourced from a sonnet
second-opinion pass on the env-scrubbing design. Highlights:

- `shell=False` always; strings are split via `shlex` (POSIX on
  POSIX, Windows-mode with quote-stripping on Windows). Card
  authors who need shell features declare the command as
  `["bash", "-c", "..."]` explicitly.
- `stdin=DEVNULL` to prevent the blocked-on-stdin timeout footgun.
- Process-group isolation: `start_new_session=True` on POSIX,
  `CREATE_NEW_PROCESS_GROUP` on Windows, so `proc.kill()` on
  timeout takes the whole tree down rather than orphaning
  grandchildren.
- `encoding="utf-8", errors="replace"` so non-UTF-8 bytes in
  captured streams don't crash the verifier on an otherwise-
  passing card.
- Scrubbed env baseline: preserve PATH, HOME, locale, TMPDIR,
  TERM, SHELL, plus PATHEXT/SYSTEMROOT/COMSPEC on Windows. Force
  CI=true, NO_COLOR=1, TERM=dumb, PYTHONUNBUFFERED=1 for
  determinism. Clear credential patterns aggressively (anything
  matching `_API_KEY$`, `_TOKEN$`, `_SECRET$`, `_PASSWORD$`,
  `^ANTHROPIC_`, `^AWS_`, `^GCP_`, `^GOOGLE_`, `^AZURE_`,
  `^OPENAI_`, plus exact matches for SSH_AUTH_SOCK,
  GH_TOKEN, GITHUB_TOKEN, DATABASE_URL, KUBECONFIG, DOCKER_HOST,
  GOOGLE_APPLICATION_CREDENTIALS, VAULT_*, SENTRY_AUTH_TOKEN,
  VERCEL_TOKEN, FLY_API_TOKEN, RAILWAY_TOKEN, NPM_TOKEN,
  PYPI_TOKEN, CARGO_REGISTRY_TOKEN). Per-item `env:` overrides
  merge on top, so a card author may re-add a specific
  credential explicitly when their check needs it.

The posture is hygiene against accidents, not a sandbox against a
determined attacker. The threat model matches the rest of v1.3.

---

## Open follow-ups

These are out of scope for this PR. Each is its own future card or
discussion item; none of them block the v1.3 cutover.

1. **Dashboard integration for `awaiting_standup_review/`.** The
   dashboard at `dashboard/` and the hosted variant at
   `app.projectnexuscode.org` need a UI surface for the new state.
   Suggested shape: a tab in the kanban that mirrors `amendments/`
   but reads `standup_reason` and `verifier_cascade_history` rather
   than `change_request`. Resolution actions on a standup card
   should be (a) approve as pass, (b) approve as fail, (c) escalate
   to amendment review. All three actions should append to
   `verifier_cascade_history` for audit. The dashboard repo lives at
   `agile-cards-board` as a submodule of this repo; the work is one
   to two cards there, ideally driven from the same v1.3 manifest.

2. **Planner-side schema validation in the /cards invocation flow.**
   `SKILL.md` section 4.5 has been updated to call
   `verifier.schema.validate_ac_items`, but the /cards skill code
   (which lives downstream of this repo as a plugin) has not yet
   been changed to actually import and run the validator at write
   time. Until that happens, planner-side validation is documented
   but not enforced; the verifier-side defense-in-depth check
   catches errors at execution time but slightly later than is
   ideal.

3. **`/cards migrate-ac-schema` rewrite tool.** Promised in
   `RUNNER_CONTRACT.md` for the v1.4 hard-deprecation moment. Trivial
   to build (regex + YAML rewrite of `acceptance_checks:` to
   `acceptance_criteria:` plus the type rename map). Not built in
   v1.3 because the v1.2 alias table makes existing cards keep
   working until v1.4.

4. **POSIX timeout for `python_assert` on Windows.** The handler's
   `_enforce_timeout` uses SIGALRM on POSIX and is a no-op on
   Windows. The default 5-second timeout is therefore not enforced
   on Windows; in practice the AST-restricted expressions complete
   in microseconds and a Windows-side timeout would be belt-and-
   suspenders, but the asymmetry is worth flagging.

5. **Runner-side completion-notes serializer.** The runner consumes
   `VerifierResult.items` and writes a structured `verifier_notes:`
   block to the card. The exact YAML shape of that block is not
   pinned in v1.3; suggested shape lives as a comment in
   `lib/verifier/runner.py`. When the runner is rebuilt to use this
   library, that shape should land in `RUNNER_CONTRACT.md` next to
   the existing notes section.

6. **Token-cost validation.** Estimates in
   `docs/design/v1.3_verifier_refactor.md` section 6 are
   order-of-magnitude. The follow-up is a synthetic sprint:

   - Build 50 representative cards across tiers 1 to 6 (mix of
     pure-deterministic, mostly-deterministic-with-one-subjective,
     and tier 5 / 6 with multiple subjective items).
   - Run each card through `verifier.runner.verify_card` with a
     real Anthropic SDK key set and `network_checks_allowed=true`.
   - Capture: total tokens per card, fraction of cards that hit the
     subjective phase, cascade depth distribution
     (haiku vs. sonnet vs. opus per item), runtime per card.
   - Compare against the v1.2 verifier baseline (which we have for
     historical sprints in `_batches/` runner logs).
   - Update the README "v1.3 verifier (token cost)" section with
     real numbers and document the methodology in
     `docs/audits/AUDIT_2026-05-XX_v1.3_token_cost.md`.

   This work is paused waiting for: (a) a real downstream runner
   that uses the library (otherwise the synthetic sprint runs
   exclusively through the test harness), and (b) Drew's call on
   whether to spend the API credits before downstream integration
   forces the issue anyway.

---

## Token-cost note (estimate only)

Pending the synthetic-sprint validation above, the design-doc
estimate stands:

| Path  | Avg tokens / card | Sprint total (50 cards) |
|-------|-------------------|-------------------------|
| v1.2  | 1.5k to 5k        | 75k to 250k             |
| v1.3  | 100 to 500        | 5k to 25k               |
| Saved |                   | ~70k to ~225k           |

The latency win (deterministic checks finish in seconds; LLM
round-trips take 30-90 seconds) is the larger practical effect.

---

## Reading order for the next agent

1. `C:\dev\SESSION_PROTOCOL.md` for session conventions.
2. This handoff.
3. `docs/design/v1.3_verifier_refactor.md` for the locked-answer
   reasoning and the sequencing context.
4. `lib/verifier/__init__.py` for the library's public API surface.
5. `lib/verifier/runner.py` for the orchestrator's dispatch logic.
6. `lib/verifier/handlers/command.py` for the security commentary
   (the load-bearing handler).
7. `RUNNER_CONTRACT.md` "Cold-read verification" section for what
   any conforming runner must implement against this library.

If you are picking up the follow-ups listed above, none of them
require restating the v1.3 contract. They are integrations on top
of a library that already exists and tests as green.
