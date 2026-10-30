"""Parse and apply a model reply under PROTOCOL.md.

Pure: `parse_reply` touches no files, and `apply_blocks` writes only inside
the scratch root it is given. All-or-nothing: a reply that breaks any rule is
a parse failure and applies nothing, so a half-applied reply never reaches
the verifier.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_BLOCKS = 20

_HEADER = re.compile(r"^<<<FILE (?P<path>.*)>>>$")
_END = "<<<END FILE>>>"


@dataclass(frozen=True)
class FileBlock:
    path: str  # relative, forward slashes, validated
    content: str


@dataclass(frozen=True)
class ParseResult:
    blocks: tuple[FileBlock, ...]
    failure: str | None  # None means it parsed; otherwise the rule broken

    @property
    def ok(self) -> bool:
        return self.failure is None


# PROTOCOL.md rule 6: anything cmd.exe could run instead of the interpreter.
_EXECUTABLE_SUFFIXES = frozenset(
    {".bat", ".cmd", ".exe", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse",
     ".wsf", ".wsh", ".msc", ".pif", ".scr"}
)
_ROOT_INTERPRETER_PREFIXES = ("python", "pytest", "py.")


def _runnable(parts: tuple[str, ...]) -> str | None:
    name = parts[-1].casefold()
    if PurePosixPath(name).suffix in _EXECUTABLE_SUFFIXES:
        return f"executable file type: {'/'.join(parts)!r}"
    if len(parts) == 1 and name.startswith(_ROOT_INTERPRETER_PREFIXES):
        return f"root-level interpreter lookalike: {parts[0]!r}"
    return None


def _bad_path(path: str) -> str | None:
    if not path:
        return "empty path"
    if "\\" in path:
        return f"backslash in path: {path!r}"
    if re.match(r"^[A-Za-z]:", path) or path.startswith("/"):
        return f"absolute path: {path!r}"
    parts = PurePosixPath(path).parts
    if ".." in parts:
        return f"'..' in path: {path!r}"
    if not parts or parts == (".",):
        return f"path names no file: {path!r}"
    return _runnable(parts)


def parse_reply(reply: str) -> ParseResult:
    """Split a reply into file blocks, or name the first rule it breaks."""
    blocks: list[FileBlock] = []
    seen: set[str] = set()
    current: str | None = None
    body: list[str] = []
    for line in reply.splitlines():
        # A byte-order mark is not whitespace to strip(); before a header it
        # failed a correct reply (#88 re-review S-F9). Markers only: content
        # lines keep every character.
        marker = line.strip().lstrip("﻿")
        header = _HEADER.match(marker)
        if header is not None:
            if current is not None:
                return ParseResult((), f"unterminated block for {current!r}")
            path = header.group("path").strip()
            problem = _bad_path(path)
            if problem:
                return ParseResult((), problem)
            normalised = str(PurePosixPath(path))
            # casefold: on Windows `a.py` and `A.py` are the same file.
            if normalised.casefold() in seen:
                return ParseResult((), f"duplicate path: {normalised!r}")
            seen.add(normalised.casefold())
            current, body = normalised, []
        elif marker == _END and current is not None:
            text = "\n".join(body)
            blocks.append(FileBlock(current, text + "\n" if body else ""))
            current = None
            if len(blocks) > MAX_BLOCKS:
                return ParseResult((), f"more than {MAX_BLOCKS} blocks")
        elif current is not None:
            body.append(line)
    if current is not None:
        return ParseResult((), f"unterminated block for {current!r}")
    if not blocks:
        return ParseResult((), "no complete block")
    return ParseResult(tuple(blocks), None)


class ApplyError(ValueError):
    """PROTOCOL.md rule 7: a block could not be applied. Scored as a parse
    failure. The scratch dir is discarded, so a partial write never reaches
    the verifier."""


@dataclass(frozen=True)
class ApplyResult:
    written: tuple[str, ...]
    touched_tests: tuple[str, ...]  # blocks under tests/, undone by the restore


def apply_blocks(blocks: tuple[FileBlock, ...], scratch: Path, seed: Path) -> ApplyResult:
    """Write `blocks` into `scratch`, then restore `seed/tests` over it.

    Raises ValueError if a path resolves outside `scratch` (a symlink in the
    seed could otherwise redirect a write); `parse_reply` already rejects
    every textual escape.
    """
    root = scratch.resolve()
    targets: list[tuple[Path, FileBlock]] = []
    for block in blocks:
        target = (root / block.path).resolve()
        if target != root and root not in target.parents:
            raise ApplyError(f"path escapes the scratch repo: {block.path!r}")
        targets.append((target, block))
    for target, block in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block.content, encoding="utf-8", newline="\n")
        except OSError as exc:  # rule 7, e.g. a parent that is a file
            raise ApplyError(f"could not write {block.path!r}: {exc}") from exc
    touched = tuple(
        b.path for b in blocks if PurePosixPath(b.path).parts[0].casefold() == "tests"
    )
    seed_tests = seed / "tests"
    if seed_tests.is_dir():
        shutil.rmtree(root / "tests", ignore_errors=True)
        shutil.copytree(seed_tests, root / "tests")
    return ApplyResult(tuple(b.path for b in blocks), touched)
