"""Verify one applied card-run, then run the canary. Spawned by run_bakeoff.

The parent assigns this process to a Windows Job Object before writing the
request, and reading the request is the first thing this does, so nothing
it (or the code under test) starts can escape the job. When the parent's
wall-clock limit passes it closes the job, which kills the whole tree: the
review found that `subprocess.run(timeout=)` in the shell handler kills only
cmd.exe and then waits forever on the pytest grandchild.

stdin: one JSON object {runner_src, card_id, card_body, worktree, env,
canary, canary_command}. stdout: one JSON object.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    request = json.loads(sys.stdin.read())  # the gate: assigned to the job before this returns
    sys.path.insert(0, request["runner_src"])
    from cards_runner.verifier import verify_card

    worktree = Path(request["worktree"])
    result = verify_card(card_id=request["card_id"], card_body=request["card_body"],
                         worktree=worktree, env=request["env"], subjective_disabled=True)
    out = {"overall_status": result.overall_status,
           "items": [{"description": i.item.get("description"), "type": i.item.get("type"),
                      "passed": i.handler_result.passed,
                      "evidence": getattr(i.handler_result, "evidence", None)}
                     for i in result.items],
           "canary": None}
    if request.get("canary") and result.overall_status == request["verdict_pass"] and result.items:
        shutil.copyfile(request["canary"], worktree / "tests" / "test_canary.py")
        env = request["env"]
        cp = subprocess.run([env["COMSPEC"], "/c", request["canary_command"]], cwd=worktree,
                            env=env, capture_output=True, text=True, errors="replace")
        out["canary"] = {"exit_code": cp.returncode, "output": (cp.stdout + cp.stderr)[-2000:]}
    sys.stdout.write(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
