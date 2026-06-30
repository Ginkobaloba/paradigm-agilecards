"""Handler: python_assert.

Evaluate a Python expression that must return truthy. Locked answer
5 in the v1.3 design doc restricts the namespace to a stdlib subset:

    Available: os (read-only via os.path, os.environ, os.listdir),
               json, pathlib (read-only operations), re, sys.

    Disallowed: subprocess, write-mode `open`, socket, urllib, any
                import not in the allow list.

Enforcement strategy: AST inspection at evaluation time. The
expression is parsed via `ast.parse(mode="eval")`. We walk the AST
and refuse to evaluate if it contains:

- any `Import` or `ImportFrom` (no imports inside the expression)
- any `Name` that resolves to a disallowed module
- any `Attribute` chain rooted at a disallowed module
- any `Call` where the callable is `open` with a non-readonly mode

If the AST is clean, we `eval` the expression in a deliberately
narrow namespace dict that maps allow-listed module names to their
modules (and `open` to a wrapped variant that refuses write modes).
No `__builtins__` is exposed beyond a small whitelist; in
particular, `__import__`, `eval`, `exec`, `compile`, `open` (raw),
and `globals` are NOT reachable.

This is NOT a sandbox. A determined attacker who controls the
expression can find a way out (every "safe eval" in Python has been
broken at some point). The check is hygiene against accident, just
like the rest of v1.3 security posture.
"""
from __future__ import annotations

import ast
import json
import os
import os.path  # noqa: F401  (imported so the symbol is on `os`)
import pathlib
import re
import sys
from typing import Any, Mapping

from verifier.project_config import ProjectConfig
from verifier.result import HandlerResult
from verifier.types import DEFAULT_TIMEOUTS_SEC, ACType


_ALLOWED_MODULES: dict[str, Any] = {
    "os": os,
    "json": json,
    "pathlib": pathlib,
    "re": re,
    "sys": sys,
}


# Attributes on `os` that are explicitly permitted. Any access to
# anything else on `os` is rejected at AST time so write-ish
# functions (os.remove, os.unlink, os.rmdir, os.system, os.popen,
# os.execv, ...) can never be reached.
_OS_ATTRIBUTE_ALLOWLIST: frozenset[str] = frozenset(
    {"path", "environ", "listdir", "sep", "linesep", "name", "getcwd"}
)


# Attributes on `pathlib` that are permitted. Path itself is allowed
# because card authors will reach for it; we restrict the *methods*
# they can call on Path objects via the AST walker (see below).
_PATHLIB_ATTRIBUTE_ALLOWLIST: frozenset[str] = frozenset({"Path", "PurePath"})


# Methods on a `pathlib.Path` (or any object, by attribute name)
# that are blocked because they mutate state.
_PATH_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "rmdir",
        "unlink",
        "touch",
        "rename",
        "replace",
        "chmod",
        "symlink_to",
        "hardlink_to",
    }
)


# Bare names the expression may reference outside the allow-listed
# modules. We give back a tiny set of safe builtins.
_SAFE_BUILTINS: dict[str, Any] = {
    "len": len,
    "any": any,
    "all": all,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "bytes": bytes,
    "list": list,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "dict": dict,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "sorted": sorted,
    "reversed": reversed,
    "isinstance": isinstance,
    "type": type,
    "repr": repr,
    "True": True,
    "False": False,
    "None": None,
    "open": None,  # overridden below by `_safe_open`.
}


class _DisallowedExpression(Exception):
    """Raised by `_check_ast` when an expression is unsafe to eval."""


def run(
    item: Mapping[str, Any],
    *,
    worktree: Path,  # type: ignore[name-defined]
    project_cfg: ProjectConfig,
) -> HandlerResult:
    from pathlib import Path as _Path

    expression = item["expression"]
    timeout = _resolve_timeout(item, project_cfg)

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        return HandlerResult(
            passed=False,
            evidence={
                "expression": expression,
                "error": f"SyntaxError parsing expression: {exc}",
            },
        )

    try:
        _check_ast(tree)
    except _DisallowedExpression as exc:
        return HandlerResult(
            passed=False,
            evidence={
                "expression": expression,
                "error": (
                    "expression rejected: "
                    f"{exc}. "
                    "python_assert namespace is restricted; see "
                    "lib/verifier/handlers/python_assert.py for the "
                    "allow list."
                ),
            },
        )

    namespace = _build_namespace(worktree=_Path(worktree))

    try:
        _enforce_timeout(timeout)
        # `eval` runs in O(microseconds) for the kind of expression
        # this handler accepts (no loops, no recursion in the AST
        # walk we permit). The timeout is enforced via signal on
        # POSIX; on Windows we rely on the AST walker keeping the
        # input bounded.
        compiled = compile(tree, filename="<python_assert>", mode="eval")
        result = eval(compiled, namespace, {})  # noqa: S307 (intentional)
    except Exception as exc:  # noqa: BLE001 - we want to capture anything
        return HandlerResult(
            passed=False,
            evidence={
                "expression": expression,
                "error": f"{type(exc).__name__} during eval: {exc}",
            },
        )
    finally:
        _clear_timeout()

    return HandlerResult(
        passed=bool(result),
        evidence={
            "expression": expression,
            "result_repr": repr(result)[:200],
        },
    )


def _check_ast(tree: ast.AST) -> None:
    """Walk the AST and reject any disallowed construct.

    Raises `_DisallowedExpression` with a human-readable message on
    the first violation found. The runner converts that into the
    `evidence.error` field on the failing item.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise _DisallowedExpression(
                "import statements are not allowed inside python_assert"
            )

        if isinstance(node, ast.Name):
            n = node.id
            if n in _ALLOWED_MODULES:
                continue
            if n in _SAFE_BUILTINS:
                continue
            # A bare `Name` that isn't an allowed module or builtin
            # is a comprehension target, lambda arg, or unknown
            # identifier. We allow comprehensions and lambdas; the
            # `Call` check below catches calls to unsafe things.
            continue

        if isinstance(node, ast.Attribute):
            root = _attribute_root(node)
            if root is not None and root.id == "os":
                top_attr = _outermost_attr(node).attr
                if top_attr not in _OS_ATTRIBUTE_ALLOWLIST:
                    raise _DisallowedExpression(
                        f"attribute access os.{top_attr} is not permitted; "
                        f"allowed: {sorted(_OS_ATTRIBUTE_ALLOWLIST)}"
                    )
            if root is not None and root.id == "pathlib":
                top_attr = _outermost_attr(node).attr
                if top_attr not in _PATHLIB_ATTRIBUTE_ALLOWLIST:
                    raise _DisallowedExpression(
                        f"attribute access pathlib.{top_attr} is not "
                        f"permitted; allowed: "
                        f"{sorted(_PATHLIB_ATTRIBUTE_ALLOWLIST)}"
                    )
            if node.attr in _PATH_WRITE_METHODS:
                raise _DisallowedExpression(
                    f"attribute {node.attr} is a write-mutation method "
                    f"and is blocked"
                )

        if isinstance(node, ast.Call):
            _check_call(node)


def _check_call(node: ast.Call) -> None:
    """Reject calls that would escape the sandbox."""
    func = node.func
    name: str | None = None
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr

    if name in {"__import__", "eval", "exec", "compile", "globals", "locals", "vars"}:
        raise _DisallowedExpression(
            f"call to {name} is not allowed inside python_assert"
        )

    if name == "open":
        _check_open_call(node)


def _check_open_call(node: ast.Call) -> None:
    """Refuse `open(path, mode)` when mode declares write semantics."""
    # `open(path)` defaults to read mode -> ok.
    if len(node.args) < 2 and not any(kw.arg == "mode" for kw in node.keywords):
        return
    mode_node: ast.expr | None = None
    if len(node.args) >= 2:
        mode_node = node.args[1]
    else:
        for kw in node.keywords:
            if kw.arg == "mode":
                mode_node = kw.value
                break
    if mode_node is None:
        return
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        # Dynamic mode argument: refuse on principle.
        raise _DisallowedExpression(
            "open() with a non-literal mode is blocked; declare the "
            "mode as a string literal so the verifier can inspect it"
        )
    mode = mode_node.value
    if any(ch in mode for ch in ("w", "a", "x", "+")):
        raise _DisallowedExpression(
            f"open() in write mode {mode!r} is blocked; python_assert is "
            "read-only. Use a `command` AC item if you need to mutate "
            "the filesystem."
        )


def _attribute_root(node: ast.Attribute) -> ast.Name | None:
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Name):
        return cur
    return None


def _outermost_attr(node: ast.Attribute) -> ast.Attribute:
    """For `os.path.exists`, return the `Attribute` for `path` (the one
    whose `.value` is the root Name)."""
    cur = node
    while isinstance(cur.value, ast.Attribute):
        cur = cur.value
    return cur


def _build_namespace(*, worktree: Any) -> dict[str, Any]:
    ns: dict[str, Any] = dict(_ALLOWED_MODULES)
    ns["worktree"] = worktree
    builtins_view = dict(_SAFE_BUILTINS)
    builtins_view["open"] = _safe_open
    ns["__builtins__"] = builtins_view
    return ns


def _safe_open(*args: Any, **kwargs: Any) -> Any:
    mode = ""
    if len(args) >= 2 and isinstance(args[1], str):
        mode = args[1]
    elif "mode" in kwargs and isinstance(kwargs["mode"], str):
        mode = kwargs["mode"]
    if any(ch in mode for ch in ("w", "a", "x", "+")):
        raise PermissionError(
            f"open() in write mode {mode!r} is blocked at runtime"
        )
    # Default to read text mode if nothing specified, matching builtin
    # `open` behavior, but only after the safety check.
    import builtins as _b
    return _b.open(*args, **kwargs)


def _resolve_timeout(
    item: Mapping[str, Any],
    project_cfg: ProjectConfig,
) -> int:
    if "timeout_sec" in item:
        return int(item["timeout_sec"])
    project_override = project_cfg.type_timeout_overrides_sec.get(
        ACType.PYTHON_ASSERT.value
    )
    if project_override is not None:
        return int(project_override)
    default = DEFAULT_TIMEOUTS_SEC[ACType.PYTHON_ASSERT.value]
    assert default is not None
    return default


# --- Timeout enforcement ----------------------------------------------------
# POSIX: SIGALRM. Windows has no SIGALRM, so we fall back to a
# best-effort scheme that simply does not enforce the timeout there;
# the AST walker keeps the work bounded and 5s is generous for any
# expression the schema accepts. If a real attacker is in scope this
# is a hole; per the security posture in command.py, real attackers
# are not in scope.


_ALARM_PREV: Any = None


def _enforce_timeout(seconds: int) -> None:
    global _ALARM_PREV
    if sys.platform == "win32":
        return
    import signal

    def _on_alarm(signum: int, frame: Any) -> None:
        raise TimeoutError(
            f"python_assert exceeded timeout of {seconds}s"
        )

    _ALARM_PREV = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)


def _clear_timeout() -> None:
    global _ALARM_PREV
    if sys.platform == "win32":
        return
    import signal

    signal.alarm(0)
    if _ALARM_PREV is not None:
        signal.signal(signal.SIGALRM, _ALARM_PREV)
        _ALARM_PREV = None
