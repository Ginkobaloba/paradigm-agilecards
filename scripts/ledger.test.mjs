// Regression tests for `node scripts/ledger.mjs check`.
//
// The case these exist for: this repo's copy of the checker predated
// paradigm-site PR #104, so it counted entries without asking whether the
// directory existed. `listEntries` returns [] for a missing directory, so
// against a DELETED or EMPTY docs/ledger the checker printed
// "ledger: 0 entries, 0 problem(s)." and exited 0. Our ci.yml runs
// `node scripts/ledger.mjs check` as a build step, so that zero case was a
// step that passed while inspecting nothing.
//
// The two zero cases are tested separately because they have different causes
// (gone versus empty) and a guard aimed at one can miss the other.
//
// Deliberately uses node:test and node:assert only. ci.yml pins Node and
// notes that scripts/ledger.mjs has no dependencies; adding a test runner to a
// Terraform repo would make that false.
//
// Run: node --test scripts/ledger.test.mjs
// Runs under `node --test` and under vitest: several repos collect scripts/**/*.test.mjs
// with vitest, which ignores tests registered with node:test and fails the file.
const { test } = process.env.VITEST ? await import("vitest") : await import("node:test");
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { FIELDS, checkEntry, runCheck, scaffold } from "./ledger.mjs";

const VALID_ENTRY = [
  "# 2026-09-19 12:00 CDT - probe entry",
  "- **Who:** test",
  "- **Change:** test",
  "- **Why:** test",
  "- **State after:** test",
  "- **Refs:** test",
  "",
].join("\n");

/** Collect log/err output instead of printing it. */
function capture() {
  const lines = [];
  return { lines, log: (m) => lines.push(String(m)), err: (m) => lines.push(String(m)) };
}

function withTempDir(fn) {
  const base = mkdtempSync(join(tmpdir(), "ledger-test-"));
  try {
    return fn(base);
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
}

test("a missing directory fails and says so", () => {
  withTempDir((base) => {
    const dir = join(base, "docs", "ledger"); // deliberately never created
    const out = capture();
    const code = runCheck(dir, out.log, out.err);
    assert.equal(code, 1, "a missing ledger directory must fail");
    assert.match(out.lines.join("\n"), /does not exist/);
    assert.match(out.lines.join("\n"), /nothing was checked/);
  });
});

test("an empty directory fails with a different message", () => {
  withTempDir((base) => {
    const dir = join(base, "docs", "ledger");
    mkdirSync(dir, { recursive: true });
    const out = capture();
    const code = runCheck(dir, out.log, out.err);
    assert.equal(code, 1, "an empty ledger directory must fail");
    assert.match(out.lines.join("\n"), /no entries found/);
  });
});

test("one valid entry passes", () => {
  withTempDir((base) => {
    const dir = join(base, "docs", "ledger");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "2026-09-19-1200-probe-entry.md"), VALID_ENTRY, "utf8");
    const out = capture();
    const code = runCheck(dir, out.log, out.err);
    assert.equal(code, 0, "a well formed entry must pass");
    assert.match(out.lines.join("\n"), /1 entries, 0 problem/);
  });
});

test("a malformed entry still fails", () => {
  withTempDir((base) => {
    const dir = join(base, "docs", "ledger");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "2026-09-19-1200-probe-entry.md"), "# wrong heading\n", "utf8");
    const out = capture();
    const code = runCheck(dir, out.log, out.err);
    assert.equal(code, 1, "a malformed entry must fail");
  });
});

// The 2026-10-13 false pass: an untouched `new` scaffold has every label and
// no content, and `check` used to report it as valid. Feed it the real
// scaffold output, not a hand-written copy, so the test follows `new`.
test("the blank scaffold from `new` fails, naming every empty field", () => {
  const { name, body } = scaffold("probe entry", new Date(2026, 9, 13, 11, 13));
  const problems = checkEntry(name, body);
  assert.equal(problems.length, FIELDS.length, problems.join("\n"));
  for (const field of FIELDS) {
    assert.ok(problems.some((p) => p.includes(`**${field}:**`) && p.endsWith("is empty")), `${field} not reported`);
  }
});

test("one blank field among filled ones fails", () => {
  const text = VALID_ENTRY.replace("- **Refs:** test", "- **Refs:**   ");
  assert.deepEqual(checkEntry("2026-09-19-1200-probe-entry.md", text), [
    '2026-09-19-1200-probe-entry.md: "- **Refs:**" is empty',
  ]);
});

test("a field whose content starts on an indented continuation line passes", () => {
  const text = VALID_ENTRY.replace("- **Change:** test", "- **Change:**\n  1. first item\n  2. second item");
  assert.deepEqual(checkEntry("2026-09-19-1200-probe-entry.md", text), []);
});

test("a blank field followed directly by the next field is not rescued by it", () => {
  const text = VALID_ENTRY.replace("- **Who:** test", "- **Who:**");
  assert.deepEqual(checkEntry("2026-09-19-1200-probe-entry.md", text), [
    '2026-09-19-1200-probe-entry.md: "- **Who:**" is empty',
  ]);
});
