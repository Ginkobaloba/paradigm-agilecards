# Bake-off exam changelog

Every version is frozen before any model produces output on it. Results from
different versions are never pooled.

## v2 (2026-10-29, pre-data)

Why: an independent review of PR #88 (three lenses: scoring, freeze
integrity, measurement validity; every finding re-run by a separate
verifier, none refuted) returned FIX FIRST with 6 high and 18 medium
findings. No model had run, so the exam is re-frozen with the fixes instead
of running known-weak cards. PROTOCOL.md "v2" holds the rules; this lists
what changed in the exam itself.

- **bk-05:** a whole-file AC on `config/tally.ini`. v1 checked only the
  named lines, so an extra key in `[report]` or `[tally]`, or a new section
  before `[export]`, passed. Blank lines before `[export]` stay free.
- **bk-08:** whole-file ACs on `CHANGELOG.md` and `README.md`. v1 accepted
  reworded 0.3.0 and 0.2.0 entries, injected paragraphs, elided bodies and a
  gutted README. Blank lines around the new heading stay free.
- Both new ACs are built from the reference solutions' own lines and were
  checked against 8 legitimate variants (must pass) and 15 scope violations
  (must fail) before this freeze.
- **canary/test_canary.py (new):** the forgery canary PROTOCOL.md v2
  describes. Never shown to a model; it is not in the seed repo.
- **PROTOCOL.md:** "Suspect passes" rewritten (canary decides, the token
  list only flags); section v2 added (content-digest identity, push before
  run, scorer identity, declared sampling, outcome categories, the infra
  error rule and validity cap, the arm-time positive control, amendment A2,
  measurement).
- **CONTROLS.md:** a control counts as passed by the verifier's verdict.
- **This file (new).**

Unchanged: SYSTEM_PROMPT.txt, the seed repo, every other card, every
reference solution.

## v1 (2026-10-28, freeze 8447e14; amendment A1 at f814f9c)

The original twelve cards, seed repo, reference solutions and protocol.
