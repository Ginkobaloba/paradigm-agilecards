#!/usr/bin/env node
// The ledger, one file per entry: docs/ledger/YYYY-MM-DD-HHMM-<slug>.md
//
//   node scripts/ledger.mjs new "<short title>"   scaffold an entry (now, local time)
//   node scripts/ledger.mjs check                 validate every entry (also run by npm test)
//   node scripts/ledger.mjs print                 all entries, oldest first, to stdout
//
// Why one file per entry: when every PR appended to one docs/LEDGER.md, any
// two open PRs conflicted, so each merge after the first needed a rebase. New
// files never conflict. There is deliberately no committed index, because a
// generated index file would conflict the same way; `print` builds the view
// on demand. docs/LEDGER.md is frozen as history (entries up to 2026-09-19).
//
// Entry format (the same fields as the frozen ledger):
//   # YYYY-MM-DD HH:MM TZ - <short title>
//   - **Who:** ...
//   - **Change:** ...
//   - **Why:** ...
//   - **State after:** ...
//   - **Refs:** ...
// The file name's date and time must match the heading's. Append only: to
// correct an entry, add a new one whose Why starts "Supersedes <file name>".
//
// Provenance: the `runCheck` zero-entry guard below is ported verbatim from
// Ginkobaloba/paradigm-site scripts/ledger.mjs at commit c23fd8052c0f0c0ed914030b08a6ad4bcc24fac5
// ("fix(ledger): check fails when it finds zero entries", PR #104). That
// repo is the source all one-file-per-entry ledger copies derive from. This
// repo's own copy (this PR, pre-fix) had the earlier inline `check` handler
// with no existsSync guard and no zero-entry guard, so a renamed or empty
// docs/ledger printed "0 entries, 0 problem(s)" and exited 0. Paths and CI
// wiring are unchanged from this repo's existing convention; only the
// zero-entry behavior is adopted from paradigm-site. paradigm-site wires the
// same function into vitest (scripts/ledger.test.mjs); this repo has no root
// test runner for scripts/, so that test file is not ported here, and
// .github/workflows/ledger.yml keeps invoking `node scripts/ledger.mjs
// check` directly.

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const LEDGER_DIR = join(ROOT, "docs", "ledger");
export const FIELDS = ["Who", "Change", "Why", "State after", "Refs"];
export const NAME_RE = /^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$/;
const HEADING_RE = /^# (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) ([A-Z]{2,5}) - (\S.*)$/;
const EM_DASH = "—";

/** Problems with one entry; empty array means valid. */
export function checkEntry(name, text) {
  const problems = [];
  const m = NAME_RE.exec(name);
  if (!m) return [`${name}: name must be YYYY-MM-DD-HHMM-<lowercase-kebab-slug>.md`];
  const [, y, mo, d, hh, mm] = m;
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const h = HEADING_RE.exec(lines[0] ?? "");
  if (!h) {
    problems.push(`${name}: first line must be "# YYYY-MM-DD HH:MM TZ - <title>"`);
  } else if (h[1] !== `${y}-${mo}-${d}` || h[2] !== hh || h[3] !== mm) {
    problems.push(`${name}: heading date/time ${h[1]} ${h[2]}:${h[3]} does not match the file name`);
  }
  for (const field of FIELDS) {
    if (!lines.some((l) => l.startsWith(`- **${field}:**`))) problems.push(`${name}: missing "- **${field}:**"`);
  }
  if (text.includes(EM_DASH)) problems.push(`${name}: contains an em dash`);
  return problems;
}

export function listEntries(dir = LEDGER_DIR) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".md") && f !== "README.md")
    .sort();
}

export function checkAll(dir = LEDGER_DIR) {
  return listEntries(dir).flatMap((f) => checkEntry(f, readFileSync(join(dir, f), "utf8")));
}

/**
 * Exit code for `check`, given the ledger directory.
 *
 * Zero entries is a FAILURE, not a pass: after a move, a rename or a wrong
 * path the old code printed "0 entries, 0 problem(s)" and exited 0, so the
 * check went green while inspecting nothing. The two zero cases are reported
 * differently, because "the directory is gone" and "the directory is empty"
 * have different causes. `log`/`err` are injectable so this is testable
 * against a temp directory with no console spying required.
 */
export function runCheck(dir = LEDGER_DIR, log = console.log, err = console.error) {
  if (!existsSync(dir)) {
    err(`ledger: ${dir} does not exist; nothing was checked (moved, renamed, or wrong path?)`);
    return 1;
  }
  const entries = listEntries(dir);
  if (entries.length === 0) {
    err(`ledger: no entries found in ${dir}; nothing was checked (every change needs an entry)`);
    return 1;
  }
  const problems = checkAll(dir);
  for (const p of problems) err(`ledger: ${p}`);
  log(`ledger: ${entries.length} entries, ${problems.length} problem(s).`);
  return problems.length ? 1 : 0;
}

/** Slug from a title: lowercase kebab, ASCII only, at most 60 characters. */
export function slugify(title) {
  const s = title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .replace(/-+$/g, "");
  if (!s) throw new Error("title must contain letters or digits");
  return s;
}

/** File name and heading for an entry at `when`, in the machine's local time zone. */
export function scaffold(title, when = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  const date = `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`;
  const hh = pad(when.getHours());
  const mm = pad(when.getMinutes());
  const tz =
    new Intl.DateTimeFormat("en-US", { timeZoneName: "short" }).formatToParts(when).find((p) => p.type === "timeZoneName")
      ?.value ?? "UTC";
  const tzAbbrev = /^[A-Z]{2,5}$/.test(tz) ? tz : "UTC";
  const name = `${date}-${hh}${mm}-${slugify(title)}.md`;
  const body = [
    `# ${date} ${hh}:${mm} ${tzAbbrev} - ${title}`,
    "- **Who:** ",
    "- **Change:** ",
    "- **Why:** ",
    "- **State after:** ",
    "- **Refs:** ",
    "",
  ].join("\n");
  return { name, body };
}

function main([cmd, ...rest]) {
  if (cmd === "check") {
    return runCheck();
  }
  if (cmd === "print") {
    for (const f of listEntries()) process.stdout.write(`${readFileSync(join(LEDGER_DIR, f), "utf8").trimEnd()}\n\n`);
    return 0;
  }
  if (cmd === "new") {
    const title = rest.join(" ").trim();
    if (!title) {
      console.error('usage: node scripts/ledger.mjs new "<short title>"');
      return 2;
    }
    const { name, body } = scaffold(title);
    const path = join(LEDGER_DIR, name);
    if (existsSync(path)) {
      console.error(`ledger: ${name} already exists`);
      return 1;
    }
    mkdirSync(LEDGER_DIR, { recursive: true });
    writeFileSync(path, body);
    console.log(`ledger: created docs/ledger/${name}`);
    return 0;
  }
  console.error("usage: node scripts/ledger.mjs <new \"title\" | check | print>");
  return 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) process.exitCode = main(process.argv.slice(2));
