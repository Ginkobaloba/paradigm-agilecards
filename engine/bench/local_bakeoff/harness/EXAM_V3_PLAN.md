# Exam v3 plan (draft, not frozen)

Drafted 2026-10-30 by the PromptBuilder seat after exam v2 saturated
(`RESULTS_EXAM_V2.md`). The stamp is `v3`: v2 is already on every A, B, C and
E row. Nothing below has been shown to any arm. This plan is the design. The
exam itself is the cards, seed repo and solutions, frozen by digest before
the first arm runs (see "Freeze").

## Why v2 could not rank

All twelve v2 cards are tier-1: one small change, mostly in one file, in a
22-file, 14 KB repo whose every file is in the prompt (about 5,600 tokens).
Every model that ran scored 100%. v3 keeps v2's scoring machinery (ACs run by
the harness, negative controls, pass^3, digest re-reads, `off_protocol`) and
changes what the cards ask.

## Card classes

The main exam has classes 1, 3 and 4, with 4 graded cards each (class 3 is 2
contradictions and 2 decoys). That's 12 graded cards plus at least 3
negative controls, 3 runs each. Class 2 is a separate sub-exam with its own
4 cards.

1. **Multi-file.** A change that is only right if it lands in 3 or more
   files: a renamed function with its callers, a new field threaded through
   a model, a serializer and a report. Scored by **held-out tests the reply
   never sees** (copied in by the harness after the reply is applied), plus
   the whole visible suite. A half-done change fails, and so does a reply
   that passes by writing its own vacuous test.
2. **Long context (over 50K tokens).** A larger seed repo, or the v2 repo
   plus realistic logs or fixtures, so that the fact the change depends on
   sits deep in the prompt. **This class runs as a separate sub-exam.** v2
   fixes `num_ctx` at 32768, and Ollama reserves the KV cache for the full
   `num_ctx` at load. At 32768, A and C already ran about 0.5 to 0.6 GB over
   a 19.5 GB lease and B had about 200 MiB spare, so at 65536 every arm is
   likely to spill to system RAM. Mixed in, that would make speed (the one
   metric that separated arms in v2) measure offload rather than the model.
   The sub-exam therefore has its own `num_ctx`, its own lease, and its
   own speed figures, reported separately and never pooled with the main
   exam's. The lease it needs is a protocol call before the freeze.
3. **Contradicted spec.** The card's text contradicts the repo (a test, a
   docstring, a config), and the right answer is to flag the contradiction
   in a named file rather than force a change. Scored without a judge model:
   - the flag must name the specific contradiction (the conflicting file
     and identifier), checked by the harness in Python, and the code must
     be unchanged;
   - **paired decoys:** cards that read as if they might conflict but do
     not, where flagging fails. So a reply that always flags to be safe
     fails half the class.
   (The other shape, several readings all accepted, was dropped: it loosens
   scoring and adds to saturation.)
4. **Real failures.** Real defects that fleet seats shipped and an
   independent verify caught, taken from AgileCards' own ledgers and verify
   reports (see "Decisions"). Each card has its own buggy context and
   held-out tests, with the bug's author model recorded. **Selection bias:**
   a defect is chosen for its author's blind spot. So provenance sits next to
   the class's results, and it's never folded silently into one arm's rank.

**Controls.** At least 3 negative controls, all scored by file checks the
harness evaluates in Python, as in v2. At least one is an impossible
ambiguous-spec card. Any control passing invalidates the arm.

## Difficulty, without contaminating the field

v2's ceiling came from never checking difficulty. Calibrating on an arm
would let that arm see the exam. The plan:

- Write **one pool** of cards per class, at least twice what the exam
  needs, by the same author and recipe, then **split it at random**: half
  is the pilot, half is the exam. Cards written after a pilot by gut feel
  carry no evidence of their difficulty. Siblings drawn from one pool at
  random do, statistically.
- Run the pilot, then **discard it**. None of its cards enter v3.
- Target: between 40% and 80% pass@1 on the pilot. If the pilot saturates,
  the whole pool is rewritten harder and split again; individual exam cards
  are never edited to fit. If it collapses near 0%, the exam can't
  separate weak models either.
- The exam half is frozen without any arm seeing it.
- Known bias: tuning on A shapes the exam around A's weaknesses, which can
  flatter or punish the other arms in ways A does not share. Running the
  pilot on two arms (A and B, one MoE and one dense) halves that risk at the
  cost of one more pilot run. Recommended.

## Metrics

v2's metrics are kept: pass@1, pass^3 with errors as fails, outcome counts,
controls, median card_seconds, decode tok/s, load, peak VRAM and lease delta.
Added:

- pass@1 and pass^3 **per card class**, labelled descriptive. At 4 cards a
  class, one card moves a class by 25%, so the rank is on the pooled main
  exam, and a class difference counts only where it holds across all runs;
- output and thinking tokens per graded card, so "thinks more" and "decodes
  slower" stay separable (v2's bk-04 and bk-10 needed this by hand);
- whether the arm stayed GPU-resident at the v3 `num_ctx`.

The refusal probe stays out of v3. A guardrail comparison needs its own
probe set, approved by Drew before it is written.

## Freeze

1. Cards, seed repo, solutions and controls committed under
   `local_bakeoff/`. `EXAM_VERSION = "v3"`, plus the `num_ctx` decision in
   PROTOCOL.md.
2. Reference solutions pass every graded card, and fail every control,
   under the harness's own scorer: the same test v2 has.
3. The digest is recorded in a ledger entry **before** the first arm runs.
   Any later change to an exam file is a new version, not an edit.

## Review

A dissenting read (Gemini via `ask-gemini.ps1`, asked to disagree, 2026-10-30)
raised five problems with the first draft. All five held up against the
draft and the v2 numbers, and each is folded in above:

- real-failure cards were biased against A;
- cards written after the pilot had no evidence of difficulty;
- the "several readings accepted" shape loosened scoring, and an always-flag
  hedge would pass the contradiction shape;
- a 65K context would break the lease and turn speed into an offload
  measurement;
- N=3 per class is noisy (now 4, and descriptive only), and multi-file cards
  scored by the visible suite
  invited vacuous self-written tests.

## Decisions (Daedalus, 2026-10-30)

- **Long-context sub-exam:** `num_ctx` 65536. Each arm takes the lease it
  measures, up to 22 GB, only in an announced window with Studio confirmed
  idle. GPU residency is reported per arm, and offload is part of this
  sub-exam's result, never pooled with the main exam's. An arm that can't
  hold 64K resident at 22 GB is reported "doesn't fit at 64K". The context
  is never shrunk to make an arm fit.
- **Real-failure cards:** drawn from real defects that fleet seats shipped
  and an independent verify then caught (ledgers and verify reports). Each
  card carries the original task, the buggy context, held-out tests, and the
  provenance (which model wrote the bug).
- **Author:** Curie writes and freezes the v3 cards. The harness author
  (PromptBuilder) doesn't write the exam it grades: that's the author-as-judge
  problem. PromptBuilder supplies the card format and harness constraints
  (`CARD_FORMAT_V3.md`), builds the harness features that format needs before
  any card exists, and sees the cards only after the freeze.
- **Arm D:** out of v3 until Drew decides on the machine configuration
  (pagefile or apps).
