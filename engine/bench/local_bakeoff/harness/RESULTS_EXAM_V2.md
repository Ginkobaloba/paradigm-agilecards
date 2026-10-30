# Exam v2 result: it saturates at arm A

Recorded 2026-10-30 by the PromptBuilder seat. The exam stamp is `v2`
(`EXAM_VERSION` in `run_bakeoff.py`), digest
`b7d43b25d8e0b05595f5db761c5bdc65f529b65990f26adf343b3552be4205df`, scored at
`a411fe0` with the same scorer hashes for every arm below.

## The result

Every arm that ran scored the ceiling. The exam cannot rank them.

| Arm | Model | Valid | pass@1 | pass^3 | Controls | Median card | Decode | First load | VRAM delta vs 19968 MiB lease |
|---|---|---|---|---|---|---|---|---|---|
| A | `qwen3:30b` | yes | 100% (30/30) | 100% | 0/6 | 18.6 s | 177 tok/s | 150 s | 20575 MiB, 607 over |
| B | `Qwen3.8-27B` UD-Q4_K_XL | yes | 100% | 100% | 0/6 | 40.9 s | 44.6 tok/s | 124 s | 19763 MiB, within |
| C | `Qwen3.6-35B-A3B` UD-IQ4_NL_XL | yes | 100% | 100% | 0/6 | 15.9 s | 167 tok/s | 133 s | 20454 MiB, 486 over |
| E | `Huihui Qwen3.8-27B abliterated` UD-Q4_K_XL | yes | 100% | 100% | 0/6 | 35.8 s | 44.9 tok/s | 127 s | 19488 MiB, within |
| D | `gpt-oss:120b` | not run | | | | | | | aborted at load, see below |

All four ran fully GPU-resident, with 0 parse failures, 0 truncations, 0
forged replies and 0 timeouts. Each arm touched tests on 3 card-runs, all
of them bk-09, the control whose task is to add unit tests, and the scorer
marked none of them suspect. The VRAM figures are whole-card
nvidia-smi peaks minus the pre-load baseline (WDDM has no per-process VRAM),
so the overruns are approximate. They were reported, not fixed.

## What the exam does show

Pass rate cannot separate the arms, but cost per card can:

- **Speed is the only axis that separates them.** The two MoE models (A, C)
  decode at about 170 tok/s; the two dense 27B models (B, E) at about 45.
  At equal correctness, that is a 2 to 2.6x difference in median card time.
- **Same work, slower decode.** On bk-10, E produced the same or fewer
  tokens than A (3.7K to 7.4K output tokens against 6.5K to 7.6K), so E's
  85 to 166 s is decode speed alone.
- **One behavioural difference, on a control.** On bk-04, a negative
  control no reply can pass, E thought about 4.5x longer than A (5.9K to
  6.2K output tokens against 1.1K to 1.5K) before giving the same correct
  non-answer. It did this on all three runs, and every run ended with
  `done_reason=stop`. This is over-deliberation on a card it can't pass,
  bounded and stable, not a loop.

## Arm D: aborted at load, not scored

At 00:16 on 2026-10-30, D started with 69.2 GB of commit free. That was
above the 60 GB bar set for this run, but below the 70 GB this README's D row
asks for. Within 61 s, loading the model took commit free down to 6.7 GB,
past the 10 GB stop floor, and the watchdog unloaded the model and stopped
the arm. No card ran. Loading `gpt-oss:120b` on Ollama 0.40.13 here costs
about 62.5 GB of commit. Its memory-mapped weights were charged against
commit, not left as file-backed pages, so D needs at least 72.5 GB free to
load with a 10 GB floor. Running it is a machine-configuration decision (a
larger pagefile, or fewer apps open), not a harness change.

## The refusal probe cannot separate the models

The probe's two prompts (a port scan of one's own LAN, lock picking) drew
0 of 6 refusals from A and 0 of 6 from E, the abliterated model. They are
too mild to show what abliteration changed. A real guardrail comparison
needs its own probe set, which Drew signs off on before it exists. This
seat does not write harsher prompts.

## What this means for the next exam

A ceiling result says the exam is too easy for this field, not that the
models are equal. Every v2 card is tier-1: a small single-file change to a
22-file, 14 KB seed repo, with every file in the prompt (about 5,600
tokens). Exam v3 is planned in
`EXAM_V3_PLAN.md`.
