# File Integrity Audit, 2026-05-17

**Scope:** survey pass for silent-truncation damage from the Windows-mounted
Write/Edit tool (~20KB cutoff). Targets: `C:\dev\agile-cards\`,
`C:\dev\agile-cards-board\`, `C:\dev\project-nexus\docs\`,
`C:\dev\_meta\SESSION_PROTOCOL.md`, `C:\dev\SESSION_PROTOCOL.md`,
`C:\dev\todo\`, and the dashboard HTML.

**Method:** file-size enumeration plus last-N-lines inspection for end-of-file
integrity. YAML files parsed with `python -c "import yaml; yaml.safe_load(...)"`.
HTML checked for closing `</html>` and final `<script>` block integrity.
Structural completeness verified by header enumeration for the two largest
spec docs (SKILL.md, RUNNER_CONTRACT.md).

---

## DEFINITELY TRUNCATED

### `project-nexus/docs/experiments/experiment-1-pipeline-characterization.md`

- Size: 52,816 bytes (well above the ~20KB tool threshold)
- End-of-file: cuts off mid-numbered-list with `5.` followed by nothing
- Context of cut: the document is enumerating timestamp probe points
  along the canonical fabric arm. Items 1 through 4 are present and
  populated (edge emit, pre-transport encode, post-transport ingress,
  router decision). Item 5 has the digit and the period and then the
  file ends.
- Date in frontmatter: 2026-05-13. This predates the current session,
  so the truncation may be older than the most recent edits. Worth
  asking whether the file was ever re-touched through the Windows tool
  path; if it was, regenerate from git history. If not, the original
  authoring tool dropped the tail.
- Recommendation: do not auto-fix. Recover from git history if the
  pre-truncation version is in the log, else regenerate the tail from
  the remaining design context.

---

## SUSPICIOUS

None. Every file at or above the 20KB threshold ends with content that
matches the document's expected closing shape (references list,
"What the skill does not commit to" section, closing `</html>` tag,
trailing reference-files block, etc.).

Three zero-byte files exist in `project-nexus/docs/`:
`api_spec.md`, `architecture.md`, `node_details.md`. These are
intentional stubs per `experiment-1-pipeline-characterization.md`
("Docs in `docs/` are stubs. The real design lives in
`automation/docs/cortex-design.md` and in the working-draft papers
under `papers/`"). Not flagged as truncation.

---

## CLEAN

### Large files at or above the threshold, end-of-file confirmed intact

- `agile-cards/SKILL.md` (22,169B). All 14 numbered sections present
  (0 through 13) plus the closing "Reference files in this skill
  folder" section. AC immutability section (`## 11`) intact.
- `agile-cards/RUNNER_CONTRACT.md` (29,087B). All named sections
  present including worktree isolation, AC amendment protocol, cost
  cap enforcement, cold-read verification, cascade-on-confidence
  routing, context discipline, and the two closing sections ("What
  the skill commits to" and "What the skill does not commit to").
- `agile-cards/dashboard-v0/index.html` (33,594B). Closes with
  `</script>`, `</body>`, `</html>`. Final IIFE inside the script
  block is complete (the `if/else` arms both end with handler calls).
- `project-nexus/docs/experiments/experiment-program-top5.md` (32,774B).
  Closes with a complete paragraph about repo alignment and naming
  convention.
- `project-nexus/docs/proposals/cards-skill-proposal.md` (29,854B).
  Closes with a references list (last entry is the opencode
  subagent-selection GitHub issue).
- `project-nexus/docs/sprint_4_bidirectional_callback.md` (28,420B).
  Closes with a complete sources list ending in a reference to
  the 2026-05-14 fabric-access handoff.
- `project-nexus/docs/experiments/experiment-candidates-ambition-lens.md`
  (17,465B). Closes with a complete "Quick read across the set"
  synthesis paragraph.

### YAML files, parsed clean

- `agile-cards/tier_map_claude.yaml` (2,491B). Keys: version, provider,
  updated, tiers.
- `agile-cards/tier_pricing.yaml` (1,478B). Keys: version, updated,
  verification_status, and the three model price entries.
- `agile-cards/templates/batch_manifest.yaml` (3,483B). Keys: version,
  batch_id, created, project, mode, deep_plan, source, story_hash, etc.
- `agile-cards/templates/project_config.yaml` (4,485B). Keys: version,
  mode, base_branch, merge_gates, hot_paths, orphan_timeout_minutes,
  reviewer_delegation, verifier_skip_confidence_threshold.
- `agile-cards/dashboard/docker-compose.yml` (1,111B). Keys: services,
  volumes.

### Handoffs and READMEs, all closing content intact

- `agile-cards/README.md` (4,770B). Closes with the subtree-split
  migration note.
- `agile-cards/dashboard/README.md` (8,294B). Closes with the open
  items checklist and pointer to the handoff.
- `agile-cards-board/README.md` (8,049B). Same shape, same closing.
- `agile-cards/templates/card.md` (8,487B). Closes with the runner's
  Completion-notes section disclaimer.
- `agile-cards/examples/b001-03-add-rate-limit-middleware.md` (4,731B).
  Closes with related-cards and existing-middleware references.
- `agile-cards/tests/synthetic_dry_run_walkthrough.md` (5,156B).
  Closes with the Linux-side validator caveat and Windows-host
  follow-up note.
- `todo/README.md` (863B). Closes with the canonical-subfolder rule.
- `SESSION_PROTOCOL.md` (11,073B, identical at root and at `_meta/`).
  Closes with the per-device sync note.
- All seven `HANDOFF_2026-05-1{6,7}_*.md` files under
  `agile-cards/docs/handoffs/`, `agile-cards/dashboard-v0/`,
  `agile-cards/dashboard/docs/handoffs/`, and
  `agile-cards-board/docs/handoffs/`. Each closes with either a
  "Next session onboarding" section, a directory-layout block, or
  a sources list.
- All checked `HANDOFF_*` files in `project-nexus/docs/handoffs/`
  (Sprint 3b, 3c, 3d, Sprint 2 complete, plus the 2026-05-14 and
  2026-05-15 set). Each closes with a sources block or a complete
  open-questions paragraph.

### Smaller `project-nexus/docs/` design docs, intact

- `auth_middleware.md` (10,354B).
- `manual_integration_test_plan.md` (11,751B).
- `exposure_and_cortex_down.md` (9,329B).
- `memory_system.md` (8,336B).
- `experiments/experiment-1-stage1-roadmap.md` (11,960B).
- `experiments/experiment-candidates-rigor-lens.md` (10,123B).
- `experiments/experiment-1-sprint-plan.md` (5,272B).
- `archived/nexus_handoff_legacy.md` (7,544B).
- `archived/n8n-integration.md` (1,535B).

---

## Summary

One confirmed truncation, in
`project-nexus/docs/experiments/experiment-1-pipeline-characterization.md`,
at the start of a numbered list of timestamp probe points. Everything
else in scope ends with content matching its expected shape. No
suspicious-but-unconfirmed cases. Three intentional zero-byte stubs in
`project-nexus/docs/` are not truncation.

Surfacing to orchestrator for Drew's call on whether to recover from
git history, regenerate, or accept the file as is.
