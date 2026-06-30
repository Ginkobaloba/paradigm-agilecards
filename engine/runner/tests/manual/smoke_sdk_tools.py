"""Chunk 4 live smoke: --invoker sdk-tools against a real Anthropic API.

Run manually after exporting `ANTHROPIC_API_KEY`. Constructs a tiny
card with one tool-friendly task (write a small file) and a 50-cent
cost cap, then drives `SdkInvoker(use_tools=True).invoke(...)`
end-to-end. Reports the actual cost, model, and whether the executor
called `report_done` with high confidence.

This is NOT in the pytest suite: it talks to the network and the LLM,
costs real money (capped), and is intended as a manual gate before
landing chunk 4. The chunk-4 handoff records the result of one run.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Make the package importable from a fresh clone without installing.
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "src"))

from cards_runner.common.types import CardSnapshot  # noqa: E402
from cards_runner.worker_stub.invoker import InvokeRequest  # noqa: E402
from cards_runner.worker_stub.sdk_invoker import SdkInvoker  # noqa: E402


CARD_TEXT = """\
# Smoke card

The acceptance criterion is satisfied by writing a file named
`hello.txt` whose contents read exactly:

    hi from the chunk-4 smoke test

Use the `file_write` tool to create this file in the worktree root,
then call `report_done` with `confidence: 0.9` and a one-sentence
summary.

## Acceptance criteria

```yaml
acceptance_criteria:
  - description: 'hello.txt exists in the worktree root'
    type: file_exists
    path: hello.txt
  - description: 'hello.txt contains the marker string'
    type: file_contains
    path: hello.txt
    needle: 'hi from the chunk-4 smoke test'
```
"""


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set in the environment; refusing to run.")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="cards-runner-smoke-"))
    print(f"workdir: {workdir}")
    try:
        snapshot = CardSnapshot(
            card_id="bSMK-01-tools",
            frontmatter={
                "id": "bSMK-01-tools",
                "title": "chunk 4 sdk-tools smoke",
                "points": 1,
                "stakes": "low",
                "difficulty": "shallow",
                "model": "claude-haiku-4-5-20251001",
                "model_floor": "haiku",
                "pin_required": False,
                "extended_thinking": False,
                "cost_cap_usd": 0.50,
            },
            body=CARD_TEXT,
        )
        # `from_env` pulls ANTHROPIC_API_KEY from os.environ and honors
        # the CARDS_RUNNER_* knobs the spawner would inject. We just
        # flip use_tools manually because the smoke is the
        # `--invoker sdk-tools` analog without the daemon dance.
        invoker = SdkInvoker.from_env()
        invoker.use_tools = True
        invoker.max_tool_turns = 6
        request = InvokeRequest(
            snapshot=snapshot,
            worktree=workdir,
            attempt_trace_id="smoke-att-1",
            trace_id="smoke-trace-1",
        )
        started = time.monotonic()
        result = invoker.invoke(request)
        elapsed = time.monotonic() - started

        print("---")
        print(f"success: {result.success}")
        print(f"halt_kind: {result.halt_kind}")
        print(f"model_used: {result.model_used}")
        print(f"actual_tokens: {result.actual_tokens}")
        print(f"actual_cost_usd: ${result.actual_cost_usd:.4f}")
        print(f"elapsed_sec: {elapsed:.1f}")
        # Verify the AC the model was supposed to satisfy.
        produced = workdir / "hello.txt"
        if produced.is_file():
            print(f"produced file exists: {produced} ({produced.stat().st_size} bytes)")
            content = produced.read_text(encoding="utf-8")
            marker = "hi from the chunk-4 smoke test"
            print(f"marker present: {marker in content}")
        else:
            print("produced file MISSING")
        print("---")
        print("completion notes preview (first 800 chars):")
        # Strip code points the cp1252 Windows console cannot encode.
        # The model occasionally emits emoji (a green check etc); we
        # only care about the structure of the notes here.
        preview = result.completion_notes_markdown[:800].encode(
            "ascii", errors="replace"
        ).decode("ascii")
        print(preview)
        return 0 if result.success else 1
    finally:
        # Keep the workdir for postmortem if the run failed; reap on
        # success so the smoke does not leave temp dirs behind.
        keep_marker = workdir / "hello.txt"
        if keep_marker.is_file():
            shutil.rmtree(workdir, ignore_errors=True)
            print(f"smoke workdir reaped: {workdir}")
        else:
            print(f"smoke workdir retained for postmortem: {workdir}")


if __name__ == "__main__":
    raise SystemExit(main())
