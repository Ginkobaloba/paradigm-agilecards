# Local-model bake-off: output protocol (frozen)

**Label for every report built on this protocol:** "text-mode, harness-applied:
measures the model's coding output, not the runner pipeline."

This file, `SYSTEM_PROMPT.txt`, the cards, the seed repo and the reference
solutions land in ONE commit before any model sees a card. The format is
frozen with the cards so it cannot be tuned toward one model. Changing any of
these files makes a new card set with a new tree hash, and results from
different tree hashes are never pooled.

## What the model receives

- **System:** `SYSTEM_PROMPT.txt`, verbatim.
- **User:** the card body: the markdown after the frontmatter, including the
  `## Acceptance criteria` block, so the model sees exactly what is checked.
  This is followed by a listing of every file in the seed repo with its full
  contents, in the same block format as the reply. There is one turn and no tools.

## What the model must reply with

Zero or more file blocks. A block is a header line, the file's COMPLETE new
contents, and an end line:

```
<<<FILE relative/path/to/file.py>>>
...the entire file, every line...
<<<END FILE>>>
```

- Both marker lines must start at column 0. Leading or trailing whitespace on
  a marker line is ignored. Nothing else may be on a marker line.
- Text outside blocks is allowed and ignored (reasoning, explanation).
- A block replaces the whole file. There are no diffs or partial edits. A
  block whose path names an existing file overwrites it; a new path creates it.
- There is no way to delete a file, run a command or commit. Cards are
  written so that file contents alone can satisfy them.

## Protected tests (the exam cannot be edited by the examinee)

- After applying a reply, the harness copies `seed_repo/tests/` back over the
  scratch dir. A block that writes under `tests/` therefore has no effect. It
  is counted and reported as "touched tests" (a behaviour signal, not a parse
  failure).
- Every pytest AC uses exactly this command shape:

  ```
  python -I -m pytest -q -p no:cacheprovider --noconftest -c tests/pytest.ini tests/<file>.py
  ```

  - `-I` (isolated mode) ignores user site-packages, `PYTHON*` env, and the
    current directory on `sys.path`, so a root-level `sitecustomize.py`,
    `pytest.py` or `.pth` written by the model cannot hijack the run.
  - `--noconftest` ignores any `conftest.py`.
  - `-c tests/pytest.ini` pins the config to the protected file, so a
    root-level `pytest.ini`, `pyproject.toml`, `tox.ini` or `setup.cfg`
    cannot add options (for example `--co`, which collects and exits 0).
  - `tests/pytest.ini` sets `pythonpath` so the tests import the package
    under test.

## Parse failure (the "output did not parse into files" metric)

A reply is a **parse failure**, scored as that card-run failing, when any of
these hold:

1. It contains no complete block.
2. A `<<<FILE` header has no matching `<<<END FILE>>>` before the next header
   or the end of the reply (unterminated).
3. A path is empty, absolute, contains `..`, a backslash or a drive letter,
   or resolves outside the scratch repo.
4. The same path appears in two blocks (compared case-insensitively, since
   Windows paths are).
5. More than 20 blocks.
6. A path names something cmd.exe could run in place of the real
   interpreter: an executable extension (`.bat .cmd .exe .com .ps1 .vbs .vbe
   .js .jse .wsf .wsh .msc .pif .scr`), or a root-level file whose name
   starts with `python`, `pytest` or `py.`. (cmd.exe searches the current
   directory before PATH, so a root `python.bat` could forge every shell AC.
   Found by the card author, 2026-10-28.)
7. Applying a block fails, for example a path whose parent is an existing
   file (`tally/legacy.py/x`).

A parse failure applies NOTHING: all-or-nothing, so a half-applied reply
never reaches the verifier. A reply that parses but fails its AC is an **AC
failure**, which is a different count.

## Verifier environment

The harness gives `verify_card` an explicit env, never the default empty one:
- `PATH` is the bench venv's `Scripts` dir plus `%SystemRoot%\System32`, and
  nothing else;
- `SystemRoot` and `COMSPEC` are set, plus `NoDefaultCurrentDirectoryInExePath=1`,
  so cmd.exe never runs a program from the scratch repo even if rule 6 missed it;
- no `PYTHON*` variables, and no credentials of any kind.

## Suspect passes (in-process forgery)

Code the model writes runs inside the pytest process, so it could write a
fake "N passed" to the output and exit, or patch pytest so failing tests
pass. Nothing inside that process can rule this out. v2 changes how it is
handled (the v1 rule scored a token hit a fail, and an independent review
showed the token list is bypassed by plain aliasing and also fails correct
code that mentions a token in a comment):

- **Canary (the mitigation that decides validity).** After a card-run passes
  every AC, the harness copies `canary/test_canary.py` (never shown to a
  model) into the scratch `tests/` and runs it with the pytest AC command
  shape. It imports every module of the package and then fails. If the run
  does not report exactly one failure with a non-zero exit, the card-run is
  **forged**: it is scored a fail, and the whole arm is invalid. This is a
  mitigation, not a proof: code that only misbehaves while one particular
  test file is collected can dodge it.
- **Token tripwire (a flag, not a score).** If any applied `.py` file
  outside `tests/` contains `os._exit`, `_getframe`, `sys.modules`,
  `import pytest`, `from pytest`, `_pytest` or `sys.settrace`, the run is
  listed as "suspect" with the file and token, for a human to read. A
  suspect flag does NOT change the score.
- The process-level isolation in "Protected tests" and "Verifier
  environment" is the defense; the canary and the tripwire only report on it.

## Scoring

Per card-run, the harness:
1. copies `seed_repo/` to a fresh scratch dir;
2. applies the parsed blocks;
3. runs `verifier.verify_card` against the scratch dir;
4. records, from the wall clock, the request start and end.

- **pass@1:** the share of card-runs passing every AC.
- **pass^3:** the share of cards passing in all 3 runs.
- Negative-control cards are reported separately and must score 0. If one
  passes, the scorer or the card is broken, and the whole arm is invalid.
- Model load time is measured once per arm, before the timed runs, and
  reported apart from them. It is never folded into per-card time.

## Amendments (pre-data)

Each amendment is committed before any model has produced output on this
card set, and says so. An amendment changes the tree hash, so the exam's
freeze moves to the amendment commit. No results exist from the earlier tree,
so nothing is split across tree hashes.

### A1 (2026-10-29): inline reasoning is stripped before parsing

**Why:** some models write their reasoning inline in the reply as
`<think>...</think>`, rather than in Ollama's separate `thinking` field. A
file block inside that reasoning would otherwise be parsed as an answer. The
original text did not say what to do. This is decided before any arm has
run, so the rule cannot be chosen to favour one model.

**Rule:**
- Before parsing, every complete `<think>...</think>` block is removed from
  the reply. Matching is case-sensitive and non-greedy, and blocks may span
  lines.
- A `<think>` with no closing tag is left in place. Its contents are not
  treated as reasoning.
- Only the reply content is ever parsed. Ollama's separate `thinking` field
  is counted, never parsed.

**Recorded per card-run:** the raw reply, the stripped reply, and
`inline_think` (true when at least one block was removed).

**Scored:** the primary score uses the stripped reply. The same runs are
also scored on the raw reply and reported as a sensitivity line. When
`inline_think` is false the two are identical by construction.

**Labeling:** every result row and every summary carries the exam freeze
commit and the commit of the verifier that scored it. A result without both
is unlabeled and is not reported. (v2 replaces the freeze commit with a
content digest; see below.)

### v2 (2026-10-29): re-freeze after independent review, pre-data

**Why:** an independent review of the v1 harness (three lenses, every
finding re-run by a separate verifier) found the freeze guard inert for
edited tracked files, the scorer unlabeled, sampling unpinned, infrastructure
errors scored as model failures, and two cards (bk-05, bk-08) that accepted
replies violating their own scope. No model had produced any output on this
card set, so the fixes are made as a new freeze rather than shipped against
known-weak cards. `CHANGELOG.md` lists every change. Where this section and
an earlier one disagree, this section wins.

**Exam identity.** The exam is every file under this directory except
`harness/`: the cards, the seed repo, the reference solutions, the canary,
this protocol, `SYSTEM_PROMPT.txt`, `CONTROLS.md`, `CHANGELOG.md` and
`.gitattributes`. Its identity is a content digest: the sha256 of the sorted
lines `<path>\0<git blob id>` over every such file found on disk (not in
git), with `.gitattributes` turning off any line-ending conversion, so the
digest is the same on every machine. The harness pins the digest and refuses
to run when the disk differs: an edited, deleted, added, renamed or
git-ignored file all refuse. Every result row and summary carries the digest.
It survives a squash or rebase merge, which a commit sha does not.

**Pre-data proof.** The freeze is pushed to the remote before any arm runs.
The server-side push time is recorded in `harness/README.md`, outside the
digest (a timestamp cannot live inside the content it stamps). The harness
also refuses to run while its results directory holds rows from a different
exam digest.

**Scorer identity.** Every result row and summary also carries the sha256
of each harness source file that scores (`run_bakeoff.py`,
`reply_parser.py`), the repo HEAD, and the Python and pytest versions of the
verifier interpreter. The harness refuses to run while the runner source or
the harness source differs from HEAD.

**Sampling (the declared policy).** Each arm runs at its vendor-recommended
sampling, written into the harness's `ARMS` table with the source, sent
explicitly on every request (load, card-runs, refusal probe), and recorded:

| arm | model | temperature | top_p | top_k | think | source |
|---|---|---|---|---|---|---|
| A | qwen3:30b | 0.6 | 0.95 | 20 | true | the tag's Ollama Modelfile params |
| B | Qwen3.8-27B UD-Q4_K_XL | 1.0 | 0.95 | 20 | true | the GGUF `general.sampling` keys |
| C | Qwen3.6-35B-A3B UD-IQ4_NL_XL | 1.0 | 0.95 | 20 | true | the GGUF `general.sampling` keys |
| D | gpt-oss:120b | 1.0 | 1.0 | (none) | "medium" | the vendor's recommendation |
| E | Huihui Qwen3.8-27B abliterated UD-Q4_K_XL | 1.0 | 0.95 | 20 | true | the GGUF `general.sampling` keys |

Every arm: `num_ctx` 32768 and `num_predict` 26624, so the longest prompt
(about 5,600 tokens) plus the whole output budget fits the context. The
seed is fixed per card-run: `20261029 + run index`, the same for every
card in a run and every arm, so three runs are three samples rather than
one sample three times. At load the harness records the Ollama version
(refusing below 0.34.0, the first release that applies GGUF sampling
defaults), the model's digest from `/api/show`, and the options it sent.
Arms with different digests for the same tag are never pooled.

**Outcomes of a card-run.** Exactly one of:
- **pass:** parsed, every AC passed, the canary failed as it must.
- **parse failure:** rules 1 to 7.
- **truncated:** the reply stopped at the output limit (`done_reason` is
  `length`, or prompt plus output tokens reached 98% of `num_ctx`). A model
  failure, reported apart from parse failures.
- **AC failure:** parsed, and an AC failed.
- **verifier timeout:** the verifier did not finish within its wall-clock
  limit (for example a reply that loops forever); the harness kills the whole
  process tree. A model failure.
- **forged:** the canary did not fail. A fail, and the arm is invalid.
- **infra error:** the model call itself failed: no connection, an HTTP
  error, a body carrying `error`, or a reply without `done: true`. Not the
  model's answer. A request that exceeds the request timeout is a model
  failure (too slow), not an infra error.

**Denominator and validity.**
- pass@1 is passes over exam card-runs that are not infra errors. Reported
  next to it: the same passes over ALL exam card-runs (infra errors counted
  as fails), and the infra error count.
- pass^3 is over cards whose runs had no infra error; the number of cards
  left out is reported.
- The arm is **INVALID** if: infra errors exceed 10% of exam card-runs; or
  any exam card has zero non-error runs; or a negative control's verdict is
  pass (counted by the verifier's verdict, whatever else was flagged); or
  the controls did not run exactly twice per run; or any card-run is forged.
- **Positive control.** Before the model is loaded, the harness scores all
  twelve reference solutions through the same verifier environment. Unless
  the ten exam solutions pass and both controls fail, it stops and runs
  nothing. This catches a broken toolchain (for example a venv without
  pytest) that would otherwise score as a weak model.

**Amendment A2: an orphan closing tag.** If the reply contains `</think>`
with no `<think>` before it (what a template that pre-fills the opening tag
produces), everything up to and including the first such `</think>` is
removed before parsing, then A1 applies to the rest. Recorded per card-run
alongside `inline_think`: `orphan_think_close`, `unclosed_think`,
`nested_think`, and `think_in_block` (a think tag inside a file block's
contents, which is left as content). The A1 raw-reply sensitivity line
covers A2 as well.

**Measurement (reported, not scored).** Per card-run: `done_reason`, load,
prompt and eval durations. Per arm: `/api/ps` residency (`size_vram` against
`size`), whole-card VRAM with its caveat written into the result (WDDM
reports no per-process figure, so it includes the desktop and anything else
on the card), a read after unload, and the time statistics over non-error
exam card-runs only. The refusal probe is reported as its own column, never
in a score or rank, and B vs E differs by abliteration AND by the
requantization of the ablated tensors, so it is not an exact isolation.
