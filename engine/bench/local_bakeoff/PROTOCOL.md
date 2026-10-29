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

Code the model writes runs inside the pytest process, so it could print a
fake "N passed" and `os._exit(0)`, or graft test functions onto a test
module. So a card-run that passes every AC is re-checked: if any applied
`.py` file outside `tests/` contains `os._exit`, `_getframe`, `sys.modules`,
`import pytest`, `from pytest`, `_pytest` or `sys.settrace`, the run is
**scored a fail** and listed as "suspect" with the file and token, for a
human to read. None of the twelve reference solutions contains one.

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
