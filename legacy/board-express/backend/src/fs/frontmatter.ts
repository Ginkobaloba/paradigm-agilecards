/**
 * Frontmatter parser + a targeted status-line rewriter.
 *
 * We use js-yaml for parsing the frontmatter block, but for *writing* we
 * deliberately do a regex replace of the `status:` line instead of
 * re-serializing the whole YAML. Reason: js-yaml will normalize quoting,
 * key order, comments, and number/string casts. The card files are
 * human-edited markdown; we don't want a status flip to rewrite comments
 * or reflow the block. A targeted line edit is the minimal, faithful
 * mutation.
 */

import yaml from "js-yaml";

export interface ParsedCard {
  readonly frontmatter: Record<string, unknown>;
  readonly body: string;
}

const FRONTMATTER_DELIM = "---";

export function parseFrontmatter(raw: string): ParsedCard {
  const text = raw.replace(/^﻿/, ""); // strip BOM if present
  const lines = text.split(/\r?\n/);
  if (lines.length === 0 || (lines[0] ?? "").trim() !== FRONTMATTER_DELIM) {
    return { frontmatter: {}, body: text };
  }

  let endIdx = -1;
  for (let i = 1; i < lines.length; i++) {
    if ((lines[i] ?? "").trim() === FRONTMATTER_DELIM) {
      endIdx = i;
      break;
    }
  }
  if (endIdx === -1) return { frontmatter: {}, body: text };

  const fmText = lines.slice(1, endIdx).join("\n");
  const bodyText = lines.slice(endIdx + 1).join("\n");

  let parsed: unknown;
  try {
    parsed = yaml.load(fmText, { schema: yaml.JSON_SCHEMA });
  } catch {
    return { frontmatter: {}, body: text };
  }

  const fm =
    parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};

  return { frontmatter: fm, body: bodyText };
}

/**
 * Replace the `status:` line in the frontmatter block with a new value.
 * If there is no `status:` line, insert one right after the opening `---`.
 * Preserves trailing comments on the line so something like
 * `status: backlog   # waiting on approval` becomes
 * `status: active   # waiting on approval`.
 */
export function rewriteStatus(raw: string, newStatus: string): string {
  return rewriteField(raw, "status", newStatus);
}

/**
 * Targeted, comment-preserving rewriter for an arbitrary scalar
 * frontmatter field. Same philosophy as `rewriteStatus`: a regex-line
 * edit, not a yaml re-serialization, because the card files are
 * hand-edited markdown and we never want to reflow them.
 *
 * Semantics:
 *   - If the field exists in the frontmatter block: replace its value,
 *     preserving any `# ...` trailing comment on that line.
 *   - If the field does NOT exist: insert a new `key: value` line right
 *     after the opening `---` fence. If the card has no frontmatter at
 *     all, a fresh fence is prepended.
 *   - If `value` is `null`: remove the line entirely (including its
 *     trailing newline). A no-op if the field wasn't there.
 *
 * The field name is validated against `/^[A-Za-z_][A-Za-z0-9_]*$/` to
 * keep the regex safe. The serialized value is the raw stringification
 * of strings/numbers/booleans; callers must pre-quote anything that
 * would confuse YAML (this rewriter doesn't try to be clever about
 * arrays, multiline strings, or objects).
 */
export function rewriteField(
  raw: string,
  key: string,
  value: string | number | boolean | null
): string {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
    throw new Error(`Invalid frontmatter key: ${key}`);
  }

  // Anchor the regex to the start of any line (multiline mode).
  const keyRe = new RegExp(
    `^(${escapeReKey(key)}\\s*:\\s*)([^\\r\\n#]*?)(\\s*(?:#.*)?)$`,
    "m"
  );
  const lineRe = new RegExp(
    `^${escapeReKey(key)}\\s*:[^\\r\\n]*\\r?\\n?`,
    "m"
  );

  if (value === null) {
    // Remove the field's line entirely. If absent, raw is unchanged.
    return raw.replace(lineRe, "");
  }

  const serialized = serializeScalar(value);

  if (keyRe.test(raw)) {
    return raw.replace(
      keyRe,
      (_m, prefix: string, _v: string, trailing: string) =>
        `${prefix}${serialized}${trailing}`
    );
  }
  // Field absent: insert right after the opening fence so the order
  // stays predictable.
  if (raw.startsWith("---\n")) {
    return raw.replace(/^---\n/, `---\n${key}: ${serialized}\n`);
  }
  if (raw.startsWith("---\r\n")) {
    return raw.replace(/^---\r\n/, `---\r\n${key}: ${serialized}\r\n`);
  }
  // No frontmatter block at all. Prepend one with just this field.
  return `---\n${key}: ${serialized}\n---\n${raw}`;
}

function escapeReKey(s: string): string {
  // The key validator already restricts us to [A-Za-z0-9_], but escape
  // anyway so future loosening of the validator doesn't silently
  // produce a malformed regex.
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function serializeScalar(value: string | number | boolean): string {
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  // Strings: leave bare unless they contain YAML-significant chars,
  // in which case double-quote and escape backslashes + double quotes.
  if (/[:#\r\n\t"'\\]/.test(value) || /^\s|\s$/.test(value)) {
    return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
  }
  return value;
}
