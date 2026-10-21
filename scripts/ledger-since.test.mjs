// Tests for `ledger check --since <base-ref>`: does this CHANGE add an entry?
//
// These run against REAL temporary git repositories, driving real `git` through
// the injectable `run` hook. Stubbing git would only prove the stub agrees with
// itself, and the behaviour under test is entirely about what git reports.
//
// The cases that matter are the failures. `runCheck` already validated the
// entries that EXIST; this exists because that cannot see a change arriving with
// none, so every "could not tell" must be a FAILURE and not a pass.
//
// Run: node --test scripts/ledger-since.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

import { requireNewEntry } from "./ledger.mjs";

const VALID_ENTRY = [
  "# 2026-09-25 12:00 CDT - probe entry",
  "- **Who:** test",
  "- **Change:** test",
  "- **Why:** test",
  "- **State after:** test",
  "- **Refs:** test",
  "",
].join("\n");

function capture() {
  const lines = [];
  return { lines, log: (m) => lines.push(String(m)), err: (m) => lines.push(String(m)) };
}

/** A real git repo with one commit on `main` and a branch off it. */
function withRepo(fn) {
  const dir = mkdtempSync(join(tmpdir(), "ledger-since-"));
  const git = (...args) => execFileSync("git", args, { cwd: dir, encoding: "utf8" });
  try {
    git("init", "--quiet", "--initial-branch=main");
    git("config", "user.email", "test@example.com");
    git("config", "user.name", "Test");
    git("config", "commit.gpgsign", "false");
    mkdirSync(join(dir, "docs", "ledger"), { recursive: true });
    writeFileSync(join(dir, "docs", "ledger", "2026-09-01-0000-base.md"), VALID_ENTRY, "utf8");
    writeFileSync(join(dir, "README.md"), "base\n", "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "base");
    git("checkout", "--quiet", "-b", "feature");
    // The injected runner, so requireNewEntry operates on THIS repo.
    const run = (args) => execFileSync("git", args, { cwd: dir, encoding: "utf8" });
    return fn({ dir, git, run });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a change that adds NO entry is a FAILURE", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "src.txt"), "code\n", "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "feat: something with no ledger entry");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    const text = out.lines.join("\n");
    assert.equal(code, 1, "no entry must fail");
    assert.match(text, /adds NO entry/);
    assert.match(text, /src\.txt/, "should name what WAS added, to make the omission obvious");
  });
});

test("a change that adds an entry passes, and names it", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "src.txt"), "code\n", "utf8");
    writeFileSync(join(dir, "docs", "ledger", "2026-09-25-1200-the-change.md"), VALID_ENTRY, "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "feat: something, with its entry");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    assert.equal(code, 0);
    assert.match(out.lines.join("\n"), /2026-09-25-1200-the-change\.md/);
  });
});

test("the exemption trailer passes AND prints the reason, so it is auditable", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "typo.txt"), "fix\n", "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "docs: fix a typo\n\nLedger: none (typo fix, no behaviour change)");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    const text = out.lines.join("\n");
    assert.equal(code, 0, "a reasoned exemption must pass");
    assert.match(text, /exempted by commit trailer/);
    assert.match(text, /typo fix, no behaviour change/, "the reason MUST reach the log");
  });
});

test("an EMPTY reason in the trailer is rejected", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "x.txt"), "x\n", "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "chore: x\n\nLedger: none ()");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    assert.equal(code, 1, "an unexplained exemption is the same as no check");
    assert.match(out.lines.join("\n"), /EMPTY reason/);
  });
});

test("a whitespace-only reason is also rejected", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "x.txt"), "x\n", "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "chore: x\n\nLedger: none (   )");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    assert.equal(code, 1, "whitespace is not a reason");
    assert.match(out.lines.join("\n"), /EMPTY reason/);
  });
});

test("an UNRESOLVABLE base ref is a FAILURE, not a pass", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "src.txt"), "code\n", "utf8");
    writeFileSync(join(dir, "docs", "ledger", "2026-09-25-1200-ok.md"), VALID_ENTRY, "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "feat: with an entry");
    const out = capture();
    // Entry IS present, so only the unresolvable base can fail this.
    const code = requireNewEntry("origin/does-not-exist", out.log, out.err, run);
    assert.equal(code, 1, "could not check must never report clean");
    assert.match(out.lines.join("\n"), /cannot resolve base ref/);
    assert.match(out.lines.join("\n"), /fetch-depth: 0/, "should name the usual cause in CI");
  });
});

// This one exists because MUTATION TESTING found it missing: changing the git-diff
// failure path to return 0 broke no test. A resolvable base ref whose diff then
// fails is a different path from an unresolvable one, and it has to fail closed
// too. Driven through the injected runner, because making real git fail on diff
// while succeeding on rev-parse is not something a fixture repo can arrange.
test("a git diff that FAILS after the base resolved is still a FAILURE", () => {
  const out = capture();
  const run = (args) => {
    if (args[0] === "rev-parse") return "abc123\n"; // base resolves fine
    throw new Error("fatal: bad revision, simulated git failure");
  };
  const code = requireNewEntry("main", out.log, out.err, run);
  assert.equal(code, 1, "a failed diff means nothing was compared, so it cannot pass");
  assert.match(out.lines.join("\n"), /git diff against 'main' failed/);
  assert.match(out.lines.join("\n"), /FAILURE/);
});

test("commit messages that cannot be read is a FAILURE", () => {
  const out = capture();
  const run = (args) => {
    if (args[0] === "rev-parse") return "abc123\n";
    if (args[0] === "diff") return "src.txt\n"; // no new entry, so it looks for a trailer
    throw new Error("fatal: simulated git log failure");
  };
  const code = requireNewEntry("main", out.log, out.err, run);
  assert.equal(code, 1, "unable to check for an exemption must not grant one");
  assert.match(out.lines.join("\n"), /could not read commit messages/);
});

test("a missing --since argument is a FAILURE", () => {
  const out = capture();
  assert.equal(requireNewEntry(undefined, out.log, out.err, () => ""), 1);
  assert.match(out.lines.join("\n"), /needs a base ref/);
});

test("an empty --since argument is a FAILURE", () => {
  const out = capture();
  assert.equal(requireNewEntry("   ", out.log, out.err, () => ""), 1);
  assert.match(out.lines.join("\n"), /needs a base ref/);
});

test("a README under docs/ledger does not count as an entry", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(join(dir, "docs", "ledger", "README.md"), "how this works\n", "utf8");
    git("add", "-A");
    git("commit", "--quiet", "-m", "docs: explain the ledger");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    assert.equal(code, 1, "the README is documentation ABOUT entries, not an entry");
  });
});

test("a MODIFIED entry does not count; the requirement is a NEW one", () => {
  withRepo(({ dir, git, run }) => {
    writeFileSync(
      join(dir, "docs", "ledger", "2026-09-01-0000-base.md"),
      VALID_ENTRY + "- **Also:** edited\n",
      "utf8"
    );
    git("add", "-A");
    git("commit", "--quiet", "-m", "docs: amend the existing entry");
    const out = capture();
    const code = requireNewEntry("main", out.log, out.err, run);
    assert.equal(code, 1, "--diff-filter=A means ADDED; editing an old entry is not recording a new change");
  });
});
