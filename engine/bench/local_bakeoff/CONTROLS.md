# Negative controls (never shown to a model)

Two of the twelve cards are negative controls: **bk-04** and **bk-09**. Their
card text reads like an ordinary tier-1 task. Each carries at least one AC that
no reply can satisfy under PROTOCOL.md, even with the AC block in view. Both
must score 0 in every run. If either passes, the scorer or the card is broken
and the whole arm is invalid (PROTOCOL.md "Scoring"). From v2 a control
counts as passed when the verifier's verdict is pass, whatever else the run
was flagged with, and the arm is also invalid unless both controls ran in
every run.

Neither control relies on a `shell` AC to fail. The decisive items are file
checks, which the harness evaluates in Python and which no file in the scratch
repo can influence. See "Why not the env-var shape" below.

## bk-04: Remove the deprecated tally.legacy module

- **Decisive AC:** `file_absent` on `tally/legacy.py`, a file present in
  `seed_repo/`.
- **Why it is unreachable:** PROTOCOL.md has no deletion ("There is no way to
  delete a file"). `harness/reply_parser.py` `apply_blocks` only ever calls
  `write_text` on block targets; the only removal it performs is
  `rmtree(scratch/tests)` followed by restoring `seed/tests`. The scratch dir
  is a fresh copy of `seed_repo/`, so `tally/legacy.py` exists before apply
  and nothing during apply can remove it. Writing an empty file, or one that
  raises on import, leaves the path present. A block naming a path *under*
  `tally/legacy.py` (treating it as a directory) raises in `mkdir` instead.
- **Best effort in `solutions/bk-04/`:** strips the import and `__all__`
  entry, replaces `legacy.py` with a one-line `raise ModuleNotFoundError`.
  Result: `file_absent` FAIL, pytest FAIL (`find_spec` still finds the file).

## bk-09: Add unit tests for weekend and negative date spans

- **Decisive AC:** two `file_contains` items on `tests/test_dates.py`
  (`^def test_is_weekend_sunday\(` and `^def test_days_between_negative\(`),
  neither of which appears in the seed copy of that file.
- **Why it is unreachable:** PROTOCOL.md "Protected tests": after applying a
  reply the harness copies `seed_repo/tests/` back over the scratch dir, so
  "a block that writes under `tests/` therefore has no effect".
  `apply_blocks` implements that as `rmtree(scratch/tests)` then
  `copytree(seed/tests)`, so the AC always reads the seed file. Casing
  (`Tests/test_dates.py`) lands in the same directory on Windows and is wiped
  too. The AC path is fixed, so no other file can stand in for it.
- **Secondary AC:** the pytest item expects `^5 passed`; the protected file
  has 3 tests, so it fails too. This one is NOT airtight on its own (in-process
  package code could inject test functions, and the `python.bat` hole below
  can forge any shell AC). The file checks carry the control.
- **Best effort in `solutions/bk-09/`:** the correct five-test file. It is
  counted as "touched tests" and discarded. Result: all three AC FAIL.

## Why not the env-var shape from the brief

The brief suggested a `shell` AC like `python -I -c "..."` that reads an
environment variable the harness never sets. Under this harness on Windows
that shape is not a valid control, for two reasons found during authoring:

1. **cwd executable hijack.** `handle_shell` runs `cmd.exe /c <command>` with
   cwd = scratch repo. cmd.exe searches the current directory before PATH, so
   a reply containing a root-level `python.bat` (`@echo 6 passed in 0.01s`,
   `@exit /b 0`) replaces the interpreter for every shell AC. Verified with
   the scrubbed env: exit 0 and forged output. `-I` cannot help because the
   real Python never starts. Setting `NoDefaultCurrentDirectoryInExePath=1`
   in the verifier env made the real interpreter run instead.
2. **Quote mangling.** `subprocess.run([comspec, "/c", cmd])` wraps the
   command in quotes and escapes inner quotes as `\"`; cmd.exe strips the
   outer pair and Python's argv parser then splits the `-c` code. A
   `python -I -c "a b"` AC fails with a SyntaxError for every reply, so it
   would score 0 for the wrong reason and prove nothing.

Pytest AC commands contain no double quotes, so (2) does not affect them;
(1) does, and is reported to the reviewer as a harness hole.
