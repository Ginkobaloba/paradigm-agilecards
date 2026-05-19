# agile-cards

A planning skill that decomposes a user story (or a pasted discussion) into
small, independently claimable cards a fleet of agents can run in parallel.

Licensed under PolyForm Noncommercial 1.0.0. See [LICENSE](./LICENSE).

`agile-cards` is the planner. It writes cards to a runtime data folder
(`C:\dev\todo\` by default) and a batch manifest alongside them. A separate
runner watches that folder, spawns executor agents at the model tier each
card was sized for, and drives cards from `backlog/` -> `active/` -> `done/`
or `blocked/`. Cards whose executor proposes an acceptance-criteria
amendment pass through `amendments/` for human review first.

The full specification is in [`SKILL.md`](./SKILL.md). The runner-side
contract is in [`RUNNER_CONTRACT.md`](./RUNNER_CONTRACT.md). The repo is the
authoritative source for both. The runtime data folder is intentionally a
separate directory and not part of this repo.

## Live dashboard

A web UI for the cards lives in
[`agile-cards-board`](https://github.com/Ginkobaloba/agile-cards-board)
(React + Express + SQLite, the production-grade companion to the
single-file `dashboard/v0/index.html` prototype in this repo).

The hosted instance runs at
[`https://app.projectnexuscode.org`](https://app.projectnexuscode.org).
Access is gated by Cloudflare Access (invite-only) and a per-user
bearer token. The marketing landing for the project lives at
[`https://projectnexuscode.org`](https://projectnexuscode.org).

## Who it's for

Anyone running multiple Claude (or other LLM) agents in parallel against a
real codebase who is tired of:

- writing the same tickets by hand every sprint
- guessing which tier of model to pin per task
- discovering after the fact that two agents serialized on the same file
- silent rollover at sprint close that hides chronic misestimation

If you're running one agent at a time on toy problems, you don't need this.

## Install

This is a Claude Code / Cowork skill. Two paths:

1. **As a Claude Code skill.** Drop this directory into your skills
   location and Claude Code will pick up `SKILL.md`. The skill itself
   resolves naming and session conventions from `C:\dev\NAMING_CONVENTIONS.md`
   (or the `_meta` fallback) and `C:\dev\SESSION_PROTOCOL.md`, so those
   should exist on the same machine.
2. **As a portfolio reference.** Read [`SKILL.md`](./SKILL.md) end-to-end
   for the planning model, [`RUNNER_CONTRACT.md`](./RUNNER_CONTRACT.md) for
   what an executor + runner have to implement, and the
   [`tests/`](./tests) folder for the synthetic dry-run that documents the
   expected planner output.

A real install script lives outside this repo (it's a Drew-environment
convention, not a per-skill concern). If you're cloning this fresh and want
it wired in, see the migration handoff at
[`docs/handoffs/HANDOFF_2026-05-17_repo-migration.md`](./docs/handoffs/HANDOFF_2026-05-17_repo-migration.md).

## Where the runtime data lives

The skill reads and writes:

- `C:\dev\todo\backlog\` cards waiting for a runner to claim them
- `C:\dev\todo\active\` cards an executor is currently working on
- `C:\dev\todo\done\` cards whose acceptance checks all passed and whose
  merge gate is satisfied
- `C:\dev\todo\blocked\` cards that finished work but can't merge (conflict,
  review pending, etc.)
- `C:\dev\todo\amendments\` cards whose executor wants to alter the
  acceptance criteria; gated on human review
- `C:\dev\todo\_batches\` per-batch manifest files

This folder is configurable per project via `<project>\.cards-config.yaml`.
The runtime data is deliberately not committed to this repo. The repo is
just the planner spec and config.

## What's in the repo

```
SKILL.md                The skill (planner spec, agent rules, schema)
RUNNER_CONTRACT.md      What an executor + runner are required to honor
tier_map_claude.yaml    Tier -> Claude model + extended-thinking
tier_pricing.yaml       Token prices used to derive USD at display time
templates/              card.md, batch_manifest.yaml, project_config.yaml
examples/               One worked example card
tests/                  Synthetic dry-run + atomic-rename verifier
docs/handoffs/          Migration + version handoffs
```

## Status

Spec is at v1.1. No executor or runner ships with this repo yet; the
contract is here, the implementation is not. The runner lives downstream
(or doesn't exist yet, depending on when you're reading this).

The sprint scheduler / dashboard is enumerated as future work in
`SKILL.md`. When that ships it will live as a git submodule of this repo,
not inline, so the planner stays small and the UI can evolve on its own
release cadence.

## License

Licensed under the [PolyForm Noncommercial License 1.0.0](./LICENSE).
The short version: anyone is free to read, study, modify, and use this
for noncommercial purposes (personal projects, research, education,
nonprofits, government). Commercial use requires a separate
arrangement. Copyright 2026 Drew Mattick.

## History

This repo was extracted from `dev-meta/skills/cards/` in May 2026 via
`git subtree split`, so the commit log preserves the original authorship
and timestamps of the planning work. See
[`docs/handoffs/HANDOFF_2026-05-17_repo-migration.md`](./docs/handoffs/HANDOFF_2026-05-17_repo-migration.md)
for the migration details.
