/**
 * Staging directory layout + atomic promotion to backlog.
 *
 * Why stage? The /cards skill writes cards directly to its target tree.
 * The submit-story surface wants a dry-run review before anything lands
 * in `backlog/`, so we redirect the skill's output to
 * `<cardsDir>/_staging/<batchId>/` and only move the files into
 * `backlog/` once the human approves.
 *
 * The promoter does the move card-by-card via fs.rename. Same-filesystem
 * renames are atomic on every supported OS, so the worst case is a
 * partial commit where the first N files landed but the (N+1)th
 * collided. We fail loudly in that case and leave the rest in staging
 * so an operator can reconcile by hand.
 */

import fs from "node:fs";
import path from "node:path";

import { config } from "../config.js";
import { parseFrontmatter } from "../fs/frontmatter.js";
import type { Manifest, ManifestCardSummary } from "./manifest.js";
import { summarize } from "./manifest.js";

/** Directory the invoker writes into, per batch. */
export function stagingDirFor(batchId: string): string {
  return path.join(config.cardsDir, "_staging", batchId);
}

/** Directory the manifest gets promoted into. */
export function batchesDirFor(batchId: string): string {
  return path.join(config.cardsDir, "_batches", batchId);
}

/** Backlog target folder. */
export function backlogDir(): string {
  return path.join(config.cardsDir, "backlog");
}

/**
 * Ensure the staging tree exists for a fresh batch and is empty. We
 * recreate it from scratch so a retry doesn't pick up half-written
 * files from a prior aborted run.
 */
export function prepareStaging(batchId: string): string {
  const dir = stagingDirFor(batchId);
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  fs.mkdirSync(dir, { recursive: true });
  // Mark the batch as planning-in-progress so the triage inbox keeps
  // its hands off until the planner lands. Removed by
  // `markStagingReady` once the manifest is read.
  fs.writeFileSync(path.join(dir, PLANNING_SENTINEL), "", "utf8");
  return dir;
}

/**
 * Sentinel file present in a staging dir while the planner is still
 * writing into it. The triage inbox skips (and its actions refuse)
 * batches that carry it.
 */
export const PLANNING_SENTINEL = ".planning";

/**
 * Clear the planning sentinel: the batch is fully written and may be
 * acted on (batch approve/cancel, or per-card triage). Idempotent.
 */
export function markStagingReady(batchId: string): void {
  try {
    fs.unlinkSync(path.join(stagingDirFor(batchId), PLANNING_SENTINEL));
  } catch {
    /* already gone */
  }
}

/** Drop the staging dir for a batch. Used on cancel. */
export function cleanupStaging(batchId: string): void {
  const dir = stagingDirFor(batchId);
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

/**
 * Read the manifest produced by the invoker. If the invoker wrote a
 * `manifest.json`, parse it. Otherwise synthesize one from the .md
 * files in the staging dir (sufficient for the dry-run preview; the
 * skill is moving toward always writing a manifest).
 */
export function readStagedManifest(
  batchId: string,
  meta: {
    story: string;
    projectPath: string | null;
    mode: "full" | "lean";
    deepPlanning: boolean;
  }
): Manifest {
  const dir = stagingDirFor(batchId);
  const manifestPath = path.join(dir, "manifest.json");

  if (fs.existsSync(manifestPath)) {
    const raw = fs.readFileSync(manifestPath, "utf8");
    const parsed = JSON.parse(raw) as unknown;
    return normalizeManifest(parsed, batchId, meta);
  }

  // Fallback: scan the directory.
  const cards = scanCards(dir);
  const totals = summarize(cards);
  return {
    batchId,
    story: meta.story,
    projectPath: meta.projectPath,
    mode: meta.mode,
    deepPlanning: meta.deepPlanning,
    cards,
    ...totals,
  };
}

function scanCards(dir: string): ManifestCardSummary[] {
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const cards: ManifestCardSummary[] = [];
  for (const e of entries) {
    if (!e.isFile() || !e.name.endsWith(".md")) continue;
    const file = path.join(dir, e.name);
    const raw = fs.readFileSync(file, "utf8");
    const { frontmatter } = parseFrontmatter(raw);
    const id = pickString(frontmatter, "id") ?? path.basename(e.name, ".md");
    cards.push({
      id,
      title: pickString(frontmatter, "title") ?? id,
      file: e.name,
      tier: pickNumber(frontmatter, "points"),
      model: pickString(frontmatter, "model"),
      estimatedTokens: pickNumber(frontmatter, "estimated_tokens"),
      dependsOn: pickStringArray(frontmatter, "depends_on"),
    });
  }
  return cards;
}

function pickString(
  fm: Record<string, unknown>,
  key: string
): string | null {
  const v = fm[key];
  return typeof v === "string" && v.length > 0 ? v : null;
}

function pickNumber(
  fm: Record<string, unknown>,
  key: string
): number | null {
  const v = fm[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function pickStringArray(
  fm: Record<string, unknown>,
  key: string
): ReadonlyArray<string> {
  const v = fm[key];
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string");
}

/**
 * Validate a parsed manifest.json against the shape we expect. If the
 * skill writes a richer shape we narrow it down here; if it writes a
 * sparser one we fill in defaults so the UI doesn't blow up.
 */
function normalizeManifest(
  parsed: unknown,
  batchId: string,
  meta: {
    story: string;
    projectPath: string | null;
    mode: "full" | "lean";
    deepPlanning: boolean;
  }
): Manifest {
  const p =
    parsed !== null && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : {};

  const cardsRaw = Array.isArray(p["cards"]) ? p["cards"] : [];
  const cards: ManifestCardSummary[] = cardsRaw
    .filter((c): c is Record<string, unknown> => c !== null && typeof c === "object")
    .map((c) => ({
      id:
        typeof c["id"] === "string"
          ? (c["id"] as string)
          : typeof c["file"] === "string"
            ? path.basename(c["file"] as string, ".md")
            : "unknown",
      title:
        typeof c["title"] === "string"
          ? (c["title"] as string)
          : typeof c["id"] === "string"
            ? (c["id"] as string)
            : "(untitled)",
      file:
        typeof c["file"] === "string"
          ? (c["file"] as string)
          : `${typeof c["id"] === "string" ? c["id"] : "card"}.md`,
      tier: typeof c["tier"] === "number" ? c["tier"] : pickNum(c, "points"),
      model: typeof c["model"] === "string" ? c["model"] : null,
      estimatedTokens:
        typeof c["estimated_tokens"] === "number" ? c["estimated_tokens"] : null,
      dependsOn: Array.isArray(c["depends_on"])
        ? (c["depends_on"] as unknown[]).filter(
            (x): x is string => typeof x === "string"
          )
        : [],
    }));

  const totals = summarize(cards);
  const histogram =
    p["histogram"] !== undefined && typeof p["histogram"] === "object" && p["histogram"] !== null
      ? coerceHistogram(p["histogram"] as Record<string, unknown>)
      : totals.histogram;

  const dependsOnEdges =
    typeof p["depends_on_edges"] === "number"
      ? (p["depends_on_edges"] as number)
      : totals.dependsOnEdges;

  const claimableCount =
    typeof p["claimable_count"] === "number"
      ? (p["claimable_count"] as number)
      : totals.claimableCount;

  return {
    batchId,
    story: meta.story,
    projectPath: meta.projectPath,
    mode: meta.mode,
    deepPlanning: meta.deepPlanning,
    cards,
    histogram,
    dependsOnEdges,
    claimableCount,
  };
}

function pickNum(c: Record<string, unknown>, key: string): number | null {
  const v = c[key];
  return typeof v === "number" ? v : null;
}

function coerceHistogram(h: Record<string, unknown>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(h)) {
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

/**
 * Promote a staged batch into backlog/. Moves every .md file in the
 * staging dir into <cardsDir>/backlog/, moves the manifest into
 * <cardsDir>/_batches/<batchId>/manifest.json, then removes the
 * staging dir.
 *
 * Returns the number of card files written. Throws on first collision
 * (target file already exists in backlog) so we never overwrite
 * something the runner is mid-claiming.
 */
export function promoteToBacklog(batchId: string): {
  cardsWritten: number;
  targetFiles: string[];
} {
  const src = stagingDirFor(batchId);
  if (!fs.existsSync(src)) {
    throw new Error(`No staging dir for batch ${batchId}`);
  }
  const target = backlogDir();
  fs.mkdirSync(target, { recursive: true });

  const entries = fs.readdirSync(src, { withFileTypes: true });
  const cards = entries.filter((e) => e.isFile() && e.name.endsWith(".md"));

  // Collision check first, so we fail before moving anything.
  for (const c of cards) {
    const dest = path.join(target, c.name);
    if (fs.existsSync(dest)) {
      throw new Error(
        `Card ${c.name} already exists in backlog. Refusing to overwrite.`
      );
    }
  }

  const targetFiles: string[] = [];
  for (const c of cards) {
    const from = path.join(src, c.name);
    const to = path.join(target, c.name);
    fs.renameSync(from, to);
    targetFiles.push(to);
  }

  // Promote the manifest too, so the batch is reconstructible later.
  const manifestSrc = path.join(src, "manifest.json");
  if (fs.existsSync(manifestSrc)) {
    const batchesTarget = batchesDirFor(batchId);
    fs.mkdirSync(batchesTarget, { recursive: true });
    fs.renameSync(manifestSrc, path.join(batchesTarget, "manifest.json"));
  }

  // Best-effort cleanup. If the staging dir still has stray files, leave
  // them and log; the caller will see the cardsWritten count anyway.
  try {
    fs.rmSync(src, { recursive: true, force: true });
  } catch {
    /* swallow */
  }

  return { cardsWritten: targetFiles.length, targetFiles };
}
