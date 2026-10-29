"""PROTOCOL.md rules, one test each. Pure: tmp dirs only, no model."""
from __future__ import annotations

from pathlib import Path

import pytest

from reply_parser import MAX_BLOCKS, apply_blocks, parse_reply


def block(path: str, *lines: str) -> str:
    return "\n".join([f"<<<FILE {path}>>>", *lines, "<<<END FILE>>>"])


def test_one_block_parses_with_its_full_content() -> None:
    r = parse_reply("Sure.\n" + block("pkg/a.py", "x = 1", "y = 2") + "\ndone")
    assert r.ok
    assert [(b.path, b.content) for b in r.blocks] == [("pkg/a.py", "x = 1\ny = 2\n")]


def test_backticks_and_fence_lookalikes_inside_a_file_survive() -> None:
    body = ["s = '```python'", "t = '<<<FILE not/a/header.py>>> inline'"]
    r = parse_reply(block("a.py", *body))
    assert r.ok and r.blocks[0].content == "\n".join(body) + "\n"


def test_marker_whitespace_is_ignored() -> None:
    r = parse_reply("  <<<FILE a.py>>>  \nx\n\t<<<END FILE>>>\t")
    assert r.ok and r.blocks[0].path == "a.py"


def test_empty_file_block_is_allowed() -> None:
    r = parse_reply(block("empty.txt"))
    assert r.ok and r.blocks[0].content == ""


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        ("no blocks here at all", "no complete block"),
        ("<<<FILE a.py>>>\nx = 1\n", "unterminated"),
        ("<<<FILE a.py>>>\nx\n<<<FILE b.py>>>\ny\n<<<END FILE>>>", "unterminated"),
        (block("a.py", "1") + "\n" + block("a.py", "2"), "duplicate"),
        (block("a.py", "1") + "\n" + block("A.py", "2"), "duplicate"),
        (block("", "x"), "empty path"),
        (block("/etc/passwd", "x"), "absolute"),
        (block("C:/dev/x.py", "x"), "absolute"),
        (block("c:x.py", "x"), "absolute"),
        (block("pkg\\a.py", "x"), "backslash"),
        (block("../outside.py", "x"), "'..'"),
        (block("pkg/../../outside.py", "x"), "'..'"),
        (block(".", "x"), "names no file"),
    ],
)
def test_each_rule_is_a_parse_failure(reply: str, why: str) -> None:
    r = parse_reply(reply)
    assert not r.ok and why in (r.failure or "")
    assert r.blocks == ()  # all-or-nothing: nothing to apply


def test_more_than_the_block_limit_fails() -> None:
    reply = "\n".join(block(f"f{i}.py", "x") for i in range(MAX_BLOCKS + 1))
    r = parse_reply(reply)
    assert not r.ok and "more than" in (r.failure or "")
    assert parse_reply("\n".join(block(f"f{i}.py", "x") for i in range(MAX_BLOCKS))).ok


def _seed(tmp: Path) -> Path:
    seed = tmp / "seed"
    (seed / "tests").mkdir(parents=True)
    (seed / "tests" / "test_a.py").write_text("def test_a():\n    assert False\n")
    (seed / "pkg.py").write_text("x = 0\n")
    return seed


def test_apply_writes_files_and_restores_the_protected_tests(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    scratch = tmp_path / "scratch"
    import shutil

    shutil.copytree(seed, scratch)
    r = parse_reply(
        block("pkg.py", "x = 1")
        + "\n" + block("tests/test_a.py", "def test_a():", "    assert True")
        + "\n" + block("Tests/test_new.py", "def test_b():", "    pass")
        + "\n" + block("new/mod.py", "y = 2")
    )
    assert r.ok
    applied = apply_blocks(r.blocks, scratch, seed)
    assert (scratch / "pkg.py").read_text() == "x = 1\n"
    assert (scratch / "new" / "mod.py").read_text() == "y = 2\n"
    # the examinee cannot edit or add tests: restored byte for byte
    assert (scratch / "tests" / "test_a.py").read_text() == "def test_a():\n    assert False\n"
    assert sorted(p.name for p in (scratch / "tests").iterdir()) == ["test_a.py"]
    assert applied.touched_tests == ("tests/test_a.py", "Tests/test_new.py")


def test_apply_refuses_a_symlink_escape(tmp_path: Path) -> None:
    seed = _seed(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (scratch / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privilege on this Windows host")
    r = parse_reply(block("link/x.py", "pwned"))
    assert r.ok
    with pytest.raises(ValueError, match="escapes"):
        apply_blocks(r.blocks, scratch, seed)
    assert not (outside / "x.py").exists()


@pytest.mark.parametrize(
    "path",
    ["python.bat", "PYTHON.CMD", "pytest.bat", "py.exe", "tally/run.ps1", "x/y/evil.EXE",
     "python", "python3.py", "pytest.ini", "py.typed"],
)
def test_rule6_runnable_lookalikes_are_parse_failures(path: str) -> None:
    r = parse_reply(block(path, "@echo 6 passed in 0.01s"))
    assert not r.ok and ("executable" in r.failure or "interpreter" in r.failure)


@pytest.mark.parametrize("path", ["tally/python_utils.py", "docs/pytest_notes.md", "pyproject.toml"])
def test_rule6_leaves_ordinary_names_alone(path: str) -> None:
    assert parse_reply(block(path, "x")).ok


def test_rule7_a_parent_that_is_a_file_is_an_apply_error(tmp_path: Path) -> None:
    from reply_parser import ApplyError

    seed = _seed(tmp_path)
    import shutil

    scratch = tmp_path / "scratch"
    shutil.copytree(seed, scratch)
    r = parse_reply(block("pkg.py/x.py", "y = 1"))
    assert r.ok
    with pytest.raises(ApplyError, match="could not write"):
        apply_blocks(r.blocks, scratch, seed)
