# Bake-off harness

Scores one model ("arm") against the frozen exam in `..` (freeze commit
`f814f9c`: the original freeze `8447e14` plus pre-data amendment A1), under
`../PROTOCOL.md`. Every result is labeled: "text-mode,
harness-applied: measures the model's coding output, not the runner
pipeline."

- `reply_parser.py`: PROTOCOL.md's reply format, parse failures and the
  protected-tests restore.
- `run_bakeoff.py`: one arm, end to end:
  1. refuse if the exam differs from the freeze;
  2. load the model once and time it separately from the runs;
  3. run every card 3 times, one turn, no tools;
  4. parse each reply and apply it to a fresh seed copy;
  5. verify with the protocol's env, then run the suspect scan;
  6. record VRAM;
  7. run the refusal probe;
  8. unload the model.

## Running an arm

Run from this directory with the runner venv (it has pytest, which the ACs
call through `PATH`):

```
$py = 'C:\dev\paradigm-agilecards\engine\runner\.venv\Scripts\python.exe'
& $py run_bakeoff.py --arm A --lease-gb <GB>
```

Each arm is pinned to one tag (`ARMS` in `run_bakeoff.py`), and another tag
is refused without `--allow-off-tag`:

| Arm | Tag |
|---|---|
| A | `qwen3:30b` |
| B | `hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL` (same quant family as E, so B vs E isolates the abliteration) |
| C | `hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_NL_XL` (19.5 GB, chosen to stay fully in VRAM) |
| D | `gpt-oss:120b` |
| E | `hf.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF:UD-Q4_K_XL` |

The script also refuses to run when the runner source differs from HEAD,
because then the verifier commit would not label what scored the run.

- `--lease-gb` re-runs the script under
  `C:\dev\paradigm-ops\tools\gpu\with_lease.py`, so the GPU lease lives
  exactly as long as the arm. Studio has priority: check `gpulease.py status`
  first. Arms run one at a time, and each unloads its model before exiting.
- Arm D (gpt-oss-120b) only in an exclusive window with 70 GB or more of
  headroom.
- Arm E: a GGUF build only, never `trust_remote_code`. The refusal probe
  runs for every arm, so E has baselines to compare against.

Output goes to `results/<arm>-<utc stamp>/` (gitignored):

- `runs.jsonl`: one line per card-run;
- `replies/`: every raw reply;
- `summary.json` and `summary.md`.

Exit codes: 0 means valid; 3 means a negative control passed, so the arm is
invalid; 2 means the exam is not the frozen one.

## Reading results

- **pass@1** is over the 10 exam cards x 3 runs. **pass^3** is the share of
  exam cards passed in all 3 runs.
- The controls (bk-04, bk-09) are reported apart and must be 0.
- A **suspect** run passed every AC but wrote a forgery token (`os._exit`,
  `sys.modules`, ...). It is scored a fail, and a human should read it.
- VRAM figures are device totals from nvidia-smi. This machine runs WDDM,
  which has no per-process VRAM, so they include the desktop.
- `refused_heuristic` is a keyword match: read the replies themselves.
- Only `message.content` is scored. Reasoning in Ollama's separate
  `thinking` field is counted (`thinking_chars`) but never parsed.
- PROTOCOL.md A1: complete `<think>...</think>` blocks are stripped before
  parsing. That is the primary score. `pass_at_1_raw` scores the same runs
  on the raw reply, as a sensitivity line, and `inline_think` marks the
  runs where the two can differ.
- Every row and summary carries `exam_freeze` and `verifier_commit`.

## Tests

`python -m pytest -q test_reply_parser.py test_run_bakeoff.py`. These use fake
models, no GPU. The key one: answering every card with its reference
solution passes all 10 exam cards and neither control.
