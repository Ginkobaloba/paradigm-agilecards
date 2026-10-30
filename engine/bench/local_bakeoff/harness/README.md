# Bake-off harness

Scores one model ("arm") against the frozen exam in `..`, under
`../PROTOCOL.md` (exam v2; `../CHANGELOG.md` lists what changed from v1).
Every result is labeled: "text-mode, harness-applied: measures the model's
coding output, not the runner pipeline."

## Exam identity

The exam is everything in `..` except this directory. Its identity is a
content digest (`EXAM_DIGEST` in `run_bakeoff.py`), not a commit, so it
survives a squash or rebase merge. v2 was frozen at `2594c81`, whose server
push time is **2026-10-29T22:17:51Z** (GitHub activity API, `push` to
`refs/heads/feat/local-bakeoff-bench`), before any arm ran. The harness
refuses to run when the exam on disk differs from the digest, when the
scorer files differ from HEAD, or when `results/` holds rows from another
exam.

- `reply_parser.py`: PROTOCOL.md's reply format, parse failures and the
  protected-tests restore.
- `verify_child.py`: the verifier, run in a Job Object child.
- `run_bakeoff.py`: one arm, end to end:
  1. refuse unless the exam, scorer and results dir are as above;
  2. score the twelve reference solutions (positive control): unless the
     ten exam solutions pass and both controls fail, run nothing;
  3. read the server: Ollama version (refuse below 0.34.0), the tag's
     digest, template hash, parameters and capabilities;
  4. load the model once, timed apart from the runs, then read `/api/ps`;
  5. run every card 3 times, one turn, no tools, with the arm's declared
     sampling and the run's seed;
  6. parse each reply, apply it to a fresh seed copy, verify, check the canary;
  7. run the refusal probe;
  8. unload the model and read VRAM once more.

## Running an arm

Run from this directory with the runner venv (it has pytest, which the ACs
call through `PATH`):

```
$py = 'C:\dev\paradigm-agilecards\engine\runner\.venv\Scripts\python.exe'
& $py run_bakeoff.py --arm A --lease-gb <GB>
```

Each arm is pinned to one tag and one sampling setting (`ARMS` in
`run_bakeoff.py`, the PROTOCOL.md v2 table). Another tag is refused without
`--allow-off-tag`. Every request sends `num_ctx` 32768, `num_predict` 26624,
the arm's temperature, top_p, top_k and think setting, and the seed
`20261029 + run`.

| Arm | Tag | Note |
|---|---|---|
| A | `qwen3:30b` | |
| B | `hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL` | same quant family as E |
| C | `hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_NL_XL` | 19.5 GB |
| D | `gpt-oss:120b` | about 65 GB: an exclusive window with 70 GB or more of headroom |
| E | `hf.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF:UD-Q4_K_XL` | a GGUF build only, never `trust_remote_code` |

**B vs E is not an exact isolation of the abliteration.** E differs from B by
the abliteration AND by the requantization of the ablated tensors (71 of 866
tensors change quant type, in blocks 17 to 52); the chat template and all
metadata keys are equal.

- `--lease-gb` re-runs the script under
  `C:\dev\paradigm-ops\tools\gpu\with_lease.py`, so the GPU lease lives
  exactly as long as the arm. Studio has priority: check `gpulease.py status`
  first. Arms run one at a time, and each unloads its model before exiting.
- The model store is on an HDD (`D:\ollama\models`), so a cold load is slow.
  It is recorded apart (`summary.load`) and never counted in a card-run's time.

Output goes to `results/<arm>-<utc stamp>/` (gitignored):

- `runs.jsonl`: one line per card-run;
- `replies/`: every raw reply, the stripped reply when A1/A2 changed it, and
  every refusal-probe reply with its thinking;
- `summary.json` and `summary.md`.

Exit codes: 0 means valid; 3 means the arm is invalid (`invalid_reasons`
says why); 2 means it refused to run.

## Where the harness departs from PROTOCOL.md's wording

PROTOCOL.md is part of the frozen exam, so these are declared here rather
than edited there:

- **Digest source.** PROTOCOL.md says the model's digest comes from
  `/api/show`. On Ollama 0.40.13 `/api/show` returns no digest, so it is read
  from `/api/tags`; `/api/show` still supplies the template hash, parameters
  and capabilities.
- **D's top_k.** The table gives "(none)". Leaving top_k out would let
  Ollama's default of 40 apply, so it is sent as 0 (no top-k cut).
- **pass^3.** PROTOCOL.md computes it over cards with no infra error. Then
  infra errors on exactly the cards an arm cannot solve RAISED its pass^3
  (0.700 to 1.000 with 3 errors in 30, inside the 10% cap; #88 re-review
  N1). The headline `pass_pow_3` counts an errored card as a fail; the
  protocol's figure is `pass_pow_3_clean_cards`, reported next to it.
- **Scorer files.** PROTOCOL.md names two (`run_bakeoff.py`,
  `reply_parser.py`); the harness hashes three, adding `verify_child.py`,
  which runs the verifier.
- **A byte-order mark** before a block header or end marker is ignored when
  the marker is matched (it is not whitespace to `strip()`). Content lines
  keep every character.
- **A harness failure on one card-run** (an exception while scoring) is
  recorded on that row as `harness_error` with outcome `infra_error`, and
  makes the arm invalid; it never stops the arm and is never the model's fail.
- **Pinned arms only.** An arm name not in `ARMS` is refused. `--model` off
  the arm's tag (with `--allow-off-tag`) or `--num-ctx` other than 32768 runs,
  is listed in `config.off_protocol`, and makes the arm invalid.

## Reading results

- **pass@1** is over exam card-runs that are not infra errors; next to it,
  the same passes over all exam card-runs (errors as fails) and the infra
  error count. **pass^3** is over all 10 exam cards, an errored card counting
  as a fail; `pass_pow_3_clean_cards` is the protocol's figure over cards
  with no infra error, with the number left out. Both controls (bk-04,
  bk-09) must never pass.
- An arm is run at 3 runs and a 1800 s request timeout (PROTOCOL.md names a
  timeout but no number). Any other `--runs` or `--timeout` is listed in
  `config.off_protocol` and makes the arm invalid.
- Each card-run has exactly one outcome: pass, parse failure, truncated, AC
  failure, verifier timeout, forged, model timeout or infra error.
- `config` in `summary.json` holds the Ollama version, the digest and every
  option sent. The digest is pinned: it is read again after load and after
  every card-run (per row, `digest`), and any change from the pre-load read
  makes the arm invalid. `pool_problems` is a helper for whoever combines
  results: it names arms that differ in digest, options, think, Ollama
  version, exam or scorer. Nothing in the harness pools results itself.
- There is no "first use" stamp. What stops results being mixed across
  exams is the refusal to run while `results/` holds rows from another exam
  digest; what dates the exam is the server push time above.
- A **suspect** run wrote a forgery token (`os._exit`, `sys.modules`, ...).
  The canary decides forgery; the token only flags the run for a human.
- **Time** is over non-error exam card-runs, with any model load inside a
  request taken out (`card_seconds`, never below 0; `load_exceeds_wall_runs`
  counts the rows where Ollama's load time was longer than the wall time);
  `reloads_in_runs` counts mid-arm loads over 1 s. Not seen by the harness:
  a reload whose `load_duration` Ollama reports as 0 or leaves out, and
  page-in from the HDD for layers offloaded to the CPU (arm D), which is not
  in `load_duration`. `residency` (`/api/ps`) says whether the model sat fully in VRAM
  (`size_vram == size`); without that, times are not comparable across arms.
- **VRAM** figures are whole-card nvidia-smi reads for one GPU (`--gpu-index`).
  This machine runs WDDM, which has no per-process VRAM, so they include the
  desktop; the caveat is written into every summary.
- The **refusal probe** (two benign prompts, three seeded samples each) is
  its own column, never in a score or rank. `refused_heuristic` is a keyword
  match on the opening of the answer with reasoning stripped; read the
  replies. It is too weak for an E-vs-baseline claim.
- Only `message.content` is scored. Reasoning in Ollama's separate
  `thinking` field is counted (`thinking_chars`) but never parsed. A1 and A2
  strip inline think tags before parsing; `pass_at_1_raw` scores the raw
  reply as a sensitivity line.
- **Known gaps in A1/A2, kept as the frozen protocol defines them:** only
  lowercase `<think>`/`</think>` are stripped, so `<THINK>`, `<thinking>`
  and gpt-oss harmony channel text that leaks into `content` (rather than
  Ollama's `thinking` field, where it belongs when `think` is set) are
  scored as written, usually a parse failure. A literal `</think>` inside a
  final file block triggers A2 and fails a correct reply. Read
  `thinking_chars` and the replies for arm D before reading its parse
  failures as skill.
- **The canary can be evaded on purpose:** code that skips its forgery when
  `test_canary` is in `sys.argv` passes undetected (#88 re-review N2). It
  catches the forgeries a model stumbles into, not one written against it;
  the negative controls still fail under that forgery.
- `summary.unload` says whether unloading returned an error and whether
  `/api/ps` still lists the model afterwards.
- Every row and summary carries `exam_version`, `exam_digest`, `scorer`
  (file hashes), `repo_head`, and the Python and pytest versions.

## Tests

`python -m pytest -q test_reply_parser.py test_run_bakeoff.py`. These use fake
models and fake servers, no GPU and no Ollama. The key one: answering every
card with its reference solution passes all 10 exam cards and neither control.
