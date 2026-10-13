// Tests for the ledger check's required-field rule.
import assert from "node:assert/strict";
import { test } from "node:test";

import { FIELDS, checkEntry, scaffold } from "./ledger.mjs";

// The 2026-10-13 false pass (cloudflare-config PR #48): an untouched "new"
// scaffold has every label and no content, and "check" reported it as valid.
// A label with nothing after it is not a field. The scaffold test feeds the
// real scaffold output, not a hand-written copy, so it follows "new".
const BLANK_FIELD_NAME = "2026-09-19-1200-probe-entry.md";
const BLANK_FIELD_BASE = [
  "# 2026-09-19 12:00 CDT - probe entry",
  "- **Who:** test",
  "- **Change:** test",
  "- **Why:** test",
  "- **State after:** test",
  "- **Refs:** test",
  "",
].join("\n");

test("the blank scaffold from new fails, naming every empty field", () => {
  const { name, body } = scaffold("probe entry", new Date(2026, 9, 13, 11, 13));
  const problems = checkEntry(name, body);
  assert.equal(problems.length, FIELDS.length, problems.join("\n"));
  for (const field of FIELDS) {
    assert.ok(problems.some((p) => p.includes("**" + field + ":**") && p.endsWith("is empty")), field + " not reported");
  }
});

test("one blank field among filled ones fails", () => {
  const text = BLANK_FIELD_BASE.replace("- **Refs:** test", "- **Refs:**   ");
  assert.deepEqual(checkEntry(BLANK_FIELD_NAME, text), [BLANK_FIELD_NAME + ': "- **Refs:**" is empty']);
});

test("a field whose content starts on an indented continuation line passes", () => {
  const text = BLANK_FIELD_BASE.replace("- **Change:** test", "- **Change:**\n  1. first item\n  2. second item");
  assert.deepEqual(checkEntry(BLANK_FIELD_NAME, text), []);
});

test("a blank field followed directly by the next field is not rescued by it", () => {
  const text = BLANK_FIELD_BASE.replace("- **Who:** test", "- **Who:**");
  assert.deepEqual(checkEntry(BLANK_FIELD_NAME, text), [BLANK_FIELD_NAME + ': "- **Who:**" is empty']);
});
