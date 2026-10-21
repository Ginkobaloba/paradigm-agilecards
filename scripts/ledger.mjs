#!/usr/bin/env node
// canonical: Ginkobaloba/paradigm-skills scripts/ledger.mjs sha256=c08a6984868610b5ffb99d1215c40cb3f485022985deec770762248ce99d1048
//
// The ledger, one file per entry: docs/ledger/YYYY-MM-DD-HHMM-<slug>.md
//
//   node scripts/ledger.mjs new "<short title>"   scaffold an entry (now, local time)
//   node scripts/ledger.mjs check                 verify this file, then validate every entry
//   node scripts/ledger.mjs check --since <ref>   also require that this change ADDS one
//   node scripts/ledger.mjs print                 all entries, oldest first, to stdout
//   node scripts/ledger.mjs self-check            only verify this file against its header
//
// ONE COPY, VENDORED. This file is byte-identical in every repo that has a
// ledger. The source is paradigm-skills scripts/ledger.mjs; each repo carries a
// committed copy (so CI never needs another checkout or the network), placed
// there by paradigm-skills scripts/sync-ledger.ps1. Line 2 holds the sha256 of
// everything else in this file, and `check` refuses to run when they disagree,
// so a local edit turns that repo's ledger check red instead of becoming a
// silent fork. To change the checker: edit it in paradigm-skills, run
// `node scripts/ledger-stamp.mjs`, merge, then sync. (Before this, 17 copies in
// 8 variants had drifted, and every fix had to be found and ported by hand.)
//
// Why one file per entry: when every PR appended to one docs/LEDGER.md, any
// two open PRs conflicted, so each merge after the first needed a rebase. New
// files never conflict. There is deliberately no committed index, because a
// generated index file would conflict the same way; `print` builds the view
// on demand.
//
// Entry format:
//   # YYYY-MM-DD HH:MM TZ - <short title>
//   - **Who:** ...
//   - **Change:** ...
//   - **Why:** ...
//   - **State after:** ...
//   - **Refs:** ...
// The file name's date and time must match the heading's. Every field needs
// content. No em or en dashes. Append only: to correct an entry, add a new one
// whose Why starts "Supersedes <file name>".

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const SELF = fileURLToPath(import.meta.url);
const ROOT = join(dirname(SELF), "..");
export const LEDGER_DIR = join(ROOT, "docs", "ledger");
export const FIELDS = ["Who", "Change", "Why", "State after", "Refs"];
export const NAME_RE = /^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$/;
const HEADING_RE = /^# (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) ([A-Z]{2,5}) - (\S.*)$/;
// Escapes, not literals, so this file contains neither character itself.
const EM_DASH = "\u2014";
const EN_DASH = "\u2013";
export const CANONICAL_RE = /^\/\/ canonical: \S+ \S+ sha256=([0-9a-f]{64})$/;

/**
 * sha256 of a ledger.mjs body: the whole file with its `// canonical:` header
 * line removed, line endings normalised to LF (a Windows checkout with
 * autocrlf must hash the same as the committed bytes).
 */
export function bodyHash(text) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const at = lines.findIndex((l) => CANONICAL_RE.test(l));
  if (at >= 0) lines.splice(at, 1);
  return createHash("sha256").update(lines.join("\n")).digest("hex");
}

/**
 * Problems with a ledger.mjs copy; empty array means it matches its header.
 * A missing header is a problem too: stripping the line must not be a way to
 * switch the check off.
 */
export function selfProblems(text) {
  const header = text.replace(/\r\n?/g, "\n").split("\n").find((l) => CANONICAL_RE.test(l));
  if (!header) return ["no `// canonical: ... sha256=<hex>` header line; this is not a synced copy"];
  const want = CANONICAL_RE.exec(header)[1];
  const got = bodyHash(text);
  if (want !== got) {
    return [
      `body sha256 ${got.slice(0, 12)} does not match the header's ${want.slice(0, 12)}: ` +
        `this copy was edited locally. Change the checker in paradigm-skills, then re-sync.`,
    ];
  }
  return [];
}

export function selfCheck(path = SELF, err = console.error) {
  const problems = selfProblems(readFileSync(path, "utf8"));
  for (const p of problems) err(`ledger: ${p}`);
  return problems.length ? 1 : 0;
}

/**
 * Does the field labelled on lines[at] say anything?
 *
 * A label with nothing after it is not a field: `new` scaffolds exactly that,
 * so before this check an untouched scaffold (every field blank) passed as a
 * valid entry. Found 2026-10-13 when one was committed and `check` went green.
 * Content may instead start on an indented continuation line under the label
 * (some entries put a numbered list there), so that counts too.
 */
function fieldHasContent(lines, at, label) {
  return Boolean(lines[at].slice(label.length).trim()) || /^\s+\S/.test(lines[at + 1] ?? "");
}

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
    const label = `- **${field}:**`;
    const at = lines.findIndex((l) => l.startsWith(label));
    if (at < 0) problems.push(`${name}: missing "${label}"`);
    else if (!fieldHasContent(lines, at, label)) problems.push(`${name}: "${label}" is empty`);
  }
  if (text.includes(EM_DASH)) problems.push(`${name}: contains an em dash`);
  if (text.includes(EN_DASH)) problems.push(`${name}: contains an en dash`);
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

/**
 * Does this change ADD a ledger entry?
 *
 * `runCheck` above validates the entries that EXIST. It cannot see that a change
 * arrived with none, so a PR that adds no entry passes it while breaking the
 * one-entry-per-change convention the check exists to protect. That gap was
 * found on 2026-09-25 on a real PR, which merged clean with no entry.
 *
 * A checker that reads the CURRENT STATE cannot answer a question about the
 * CHANGE, so this one reads the diff instead.
 *
 * FAILS CLOSED. If the base ref cannot be resolved, or git cannot be run, that
 * is a FAILURE and not a pass: "could not check" reported as green is the whole
 * family of defect this is here to stop.
 *
 * Escape hatch: a commit trailer `Ledger: none (<reason>)` on any commit in the
 * range. The reason is mandatory and must be non-empty, and it is printed into
 * the log so the exemption is auditable after the fact. Deliberately NOT a
 * label: labels are silent and can be edited once CI has gone green.
 *
 * `run` is injectable so the tests drive it against real temporary git repos
 * without stubbing git itself.
 */
export function requireNewEntry(baseRef, log = console.log, err = console.error, run = undefined) {
  const git = run || ((args) => execFileSync("git", args, { cwd: ROOT, encoding: "utf8" }));

  if (!baseRef || !String(baseRef).trim()) {
    err("ledger: --since needs a base ref; nothing was compared, so this is a failure.");
    return 1;
  }

  // Resolve first, and fail closed. A shallow clone (actions/checkout defaults
  // to fetch-depth 1) will NOT have the base ref, and the diff would otherwise
  // throw or silently compare against nothing.
  try {
    git(["rev-parse", "--verify", `${baseRef}^{commit}`]);
  } catch {
    err(
      `ledger: cannot resolve base ref '${baseRef}', so it was not possible to tell ` +
        `whether this change adds an entry. Treating that as a FAILURE. ` +
        `If CI checks out shallow, fetch the base ref (fetch-depth: 0).`
    );
    return 1;
  }

  let added = [];
  try {
    added = git(["diff", "--name-only", "--diff-filter=A", `${baseRef}...HEAD`])
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
  } catch (e) {
    err(`ledger: git diff against '${baseRef}' failed (${e.message}); treating as a FAILURE.`);
    return 1;
  }

  const newEntries = added.filter((f) => /^docs\/ledger\/.+\.md$/.test(f) && !f.endsWith("README.md"));
  if (newEntries.length > 0) {
    log(`ledger: this change adds ${newEntries.length} entry(ies): ${newEntries.join(", ")}`);
    return 0;
  }

  // No new entry. The only way through is an explicit, reasoned exemption.
  let messages = "";
  try {
    messages = git(["log", "--format=%B", `${baseRef}..HEAD`]);
  } catch (e) {
    err(`ledger: could not read commit messages for the exemption trailer (${e.message}); FAILURE.`);
    return 1;
  }

  // Match the trailer even when the reason is empty, so an empty reason can be
  // REJECTED explicitly rather than falling through to "no trailer found".
  const trailer = messages.match(/^[ \t]*Ledger:[ \t]*none[ \t]*\(([^)]*)\)[ \t]*$/im);
  if (trailer) {
    const reason = trailer[1].trim();
    if (!reason) {
      err(
        "ledger: found a `Ledger: none ()` trailer with an EMPTY reason. " +
          "The reason is mandatory, because an unexplained exemption is the same as no check."
      );
      return 1;
    }
    log(`ledger: no entry, exempted by commit trailer. Reason: ${reason}`);
    return 0;
  }

  err(
    `ledger: this change adds NO entry under docs/ledger/ and carries no exemption. ` +
      `Every change needs an entry (see docs/ledger/README.md). ` +
      `Add one with \`node scripts/ledger.mjs new "<short title>"\`, or, if this change ` +
      `truly needs none, put a trailer \`Ledger: none (<reason>)\` in a commit message.`
  );
  if (added.length) err(`ledger: files added by this change: ${added.join(", ")}`);
  return 1;
}

function main([cmd, ...rest]) {
  if (cmd === "self-check") return selfCheck();
  if (cmd === "check") {
    // Verify this file first: a locally edited checker's verdict on the
    // entries is not the fleet's verdict, so it must not run at all.
    if (selfCheck()) return 1;
    // `--since <ref>` adds the diff-based requirement on top of the directory
    // check. Without it (pushes to main) the behaviour is unchanged.
    const i = rest.indexOf("--since");
    const code = runCheck();
    if (i === -1) return code;
    const sinceCode = requireNewEntry(rest[i + 1]);
    return code || sinceCode;
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
  console.error('usage: node scripts/ledger.mjs <new "title" | check [--since <ref>] | print | self-check>');
  return 2;
}

if (process.argv[1] && SELF === process.argv[1]) process.exitCode = main(process.argv.slice(2));
