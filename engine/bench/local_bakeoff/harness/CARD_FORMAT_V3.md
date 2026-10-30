# Exam v3 card format and harness constraints

For the v3 exam author (Curie). Written by the harness author (PromptBuilder),
who does not write or see v3 cards before the freeze (`EXAM_V3_PLAN.md`,
"Decisions"). This file says what the harness can load and score. Which
cards to write is the author's call alone. Nothing here suggests task
content.

Where this file and `EXAM_V3_PLAN.md` disagree, ask Daedalus: the plan holds
the design decisions, and this file holds the mechanics.

## Layout

v3 is a new exam root, so the frozen v2 exam stays untouched and comparable:

```
engine/bench/local_bakeoff_v3/
  PROTOCOL.md          # v3's rules; start from v2's PROTOCOL.md (see "Protocol")
  SYSTEM_PROMPT.txt    # copy v2's verbatim unless there is a reason not to
  CONTROLS.md          # which card ids are negative controls, and why each can't pass
  PROVENANCE.md        # one entry per real-failure card (see "Provenance")
  main/                # the main exam (classes 1, 3, 4)
    cards/<id>.md
    seeds/<id>/...     # the card's starting repo, shown in full in the prompt
    heldout/<id>/...   # optional; copied in after the reply, never shown
    solutions/<id>/... # reference reply files
  longctx/             # the long-context sub-exam (class 2), same shape
    cards/ seeds/ heldout/ solutions/
```

- **One seed per card.** v2 shared one seed repo across all cards. In v3
  each card has its own `seeds/<id>/`, so a real-failure card can carry its
  own buggy context. Two cards may still use identical seeds.
- **Held-out files** (`heldout/<id>/`) are copied over the scratch repo
  AFTER the reply is applied and BEFORE the ACs run. They overwrite any file
  the reply wrote at the same path. They are never in the prompt. Use them
  for tests the reply must not see or be able to edit.
- **The digest** covers everything under `local_bakeoff_v3/`. Any change
  after the freeze is a new version.

## Card file

The same format as v2's `cards/bk-01.md`: YAML frontmatter, then a markdown
body with `## Context`, `## Scope`, `## Out of scope`, a fenced
`## Acceptance criteria` YAML block, and optional `## Pointers`. The body is
what the model sees. The frontmatter's `id` must equal the file name and
the seed directory name. Note that the v2 cards' `project:` field holds an
absolute machine path. v3 cards should not; the harness sets the project.

## Acceptance criteria: what the scorer can check

The scorer is the runner's own deterministic verifier. Five AC types:

| type | checks |
|---|---|
| `file_exists` | a path exists |
| `file_absent` | a path does not exist |
| `file_contains` | a file contains a string (or a regex) |
| `file_lacks` | a file does not contain a string (or a regex) |
| `shell` | a command's output and exit code, run from the scratch repo root |

- Shell ACs run tests the way v2 does:
  `python -I -m pytest -q -p no:cacheprovider --noconftest -c tests/pytest.ini <tests>`,
  with `regex: true` and `expect_contains` anchored on the pytest summary
  line. That way a test the reply deleted, or made skip, can't pass.
- No subjective (model-judged) ACs. Scoring is deterministic.
- **Controls must fail on file checks**, which the harness evaluates in
  Python, not on a shell AC. A reply can influence what a shell command
  does, but not a Python file check (v2 `CONTROLS.md`, "Why not the env-var
  shape").
- A contradiction-flag card checks the flag with `file_contains` (or
  `file_exists` plus `file_contains`), and that the code is unchanged with
  `file_lacks` or a held-out test.

## What a reply can do

From v2's PROTOCOL.md, unchanged unless v3's protocol changes it:

- One turn, no tools. The reply is whole-file `<<<FILE path>>>` ...
  `<<<END FILE>>>` blocks, each replacing or creating one file.
- There is no way to delete a file, run a command, or commit. A card whose
  right answer is "remove X" must accept an emptied file or a stated
  marker. v2's bk-04 is a control precisely because deletion isn't
  expressible.
- Paths must be relative, with no `..`, backslash or drive letter.

## Prompt budget (a hard limit)

The prompt is the card body plus every file in the card's seed, in block
format. Measure it with a real tokenizer or the harness's own count, not by
eye.

The real limit is that the prompt plus the reply's actual output (thinking
included) must fit in `num_ctx`. `num_predict` caps the output. v2 measured:
prompts up to 6,214 tokens; graded replies up to 7,610 output tokens (A,
bk-10); one control reply of 19,311 (B, bk-04).

| exam | num_ctx | num_predict | prompt size that leaves v2's worst output room |
|---|---|---|---|
| main | 32768 | 26624 | about 6,000 tokens, as in v2 |
| longctx | 65536 | 26624 if unchanged | about 38,900 tokens |

**Open conflict:** the plan wants long-context cards over 50K tokens, but
65536 minus a 26624 output budget leaves about 38.9K. Lowering `num_predict`
to make room isn't free: 12288 would leave about 53K, but it would have
truncated B's 19.3K-token reply on a v2 control. Thinking models spend their
output budget on thinking. The choices are a smaller `num_predict` (with
truncation recorded as its own outcome, as the harness already does) or a
target below 39K. That's Daedalus's call, before any long-context card is
written.

Main-exam real-failure cards have to fit their buggy context into about 6K
tokens, or move to the sub-exam.

## Reference solutions

`solutions/<id>/` holds the files a correct reply would write. The harness
test that v2 has (`test_reference_solutions_pass_the_exam_and_never_the_controls`)
must pass on v3 before the freeze:

- every graded card's solution passes all its ACs, held-out files included;
- no control passes, with its solution or with an empty reply.

## Provenance (real-failure cards)

One `PROVENANCE.md` entry per real-failure card:

- the source (ledger entry or verify report path, and the commit or PR);
- which model wrote the defect;
- which verify caught it;
- what was changed to fit the seed.

Only AgileCards' own records are valid sources.

## Freeze

1. All files committed under `local_bakeoff_v3/`, and the harness test
   above green.
2. The digest is recorded in a ledger entry before the first arm or pilot
   runs. The pilot split (`EXAM_V3_PLAN.md`, "Difficulty") happens before
   the freeze: only the exam half is committed here. The pilot half goes in
   its own root (`local_bakeoff_v3_pilot/`, same layout) so the harness can
   run it. PromptBuilder will see pilot cards when it runs them; they are
   discarded afterwards and are not exam cards.
3. Tell PromptBuilder the digest. The harness pins it, as it pins v2's.
