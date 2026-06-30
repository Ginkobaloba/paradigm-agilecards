/**
 * Triage inbox (roadmap 2.4): per-card actions on staged batches.
 *
 * The submit flow stages planner output under `_staging/<batchId>/` and
 * v0 offered only batch-level approve / cancel. The triage inbox makes
 * the staging area a durable pre-backlog lane and adds per-card
 * resolution:
 *
 *   - promote: move one staged .md into `backlog/` (rank appended by
 *     the route, chokidar announces it to the board).
 *   - decline: move the file to `_declined/<batchId>/`. Non-destructive
 *     on purpose; declined agent output is cheap to keep and an
 *     operator can resurrect it by hand. A delete can come later.
 *   - merge: append the staged card's body to an existing card as an
 *     "Absorbed from triage" section, then decline the staged file so
 *     the provenance is kept.
 *
 * Everything reads from DISK, not the stories route's in-memory pending
 * map: staged batches must survive a server restart to be a usable
 * inbox, and the map's TTL only bounds the dry-run record.
 *
 * File-name discipline: routes pass the staged file's basename. We
 * reject anything that is not a plain `<name>.md` basename so the
 * batchId/fileName pair can never escape the staging tree.
 */

import fs from "node:fs";
import path from "node:path";

import { config } from "../config.js";
import { parseFrontmatter } from "../fs/frontmatter.js";
import {
  PLANNING_SENTINEL,
  backlogDir,
  batchesDirFor,
  stagingDirFor,
} from "./staging.js";

/** Where declined staged cards are parked, per batch. */
export function declinedDirFor(batchId: string): string {
  return path.join(config.cardsDir, "_declined", batchId);
}

const STAGING_ROOT = (): string => path.join(config.cardsDir, "_staging");

/** Max characters of card body surfaced in the inbox list. */
const EXCERPT_CHARS = 280;

function batchIsPlanning(batchId: string): boolean {
  return fs.existsSync(
    path.join(stagingDirFor(batchId), PLANNING_SENTINEL)
  );
}

export interface TriageCard {
  readonly id: string;
  readonly title: string;
  readonly file: string; // basename within the staging dir
  readonly bodyExcerpt: string;
  readonly tier: number | null;
  readonly model: string | null;
  readonly estimatedTokens: number | null;
  readonly dependsOn: ReadonlyArray<string>;
}

export interface TriageBatch {
  readonly batchId: string;
  readonly story: string | null;
  readonly cards: ReadonlyArray<TriageCard>;
}

/**
 * Guard a client-supplied staged-file name. Must be a bare `.md`
 * basename: no separators, no traversal, nothing hidden.
 */
export function isSafeStagedName(name: string): boolean {
  return (
    /^[A-Za-z0-9][A-Za-z0-9._-]*\.md$/.test(name) &&
    !name.includes("..") &&
    path.basename(name) === name
  );
}

function isSafeBatchId(batchId: string): boolean {
  return (
    /^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(batchId) &&
    !batchId.includes("..") &&
    path.basename(batchId) === batchId
  );
}

function assertSafe(batchId: string, fileName?: string): void {
  if (!isSafeBatchId(batchId)) {
    throw new TriageError(400, `invalid batchId: ${batchId}`);
  }
  if (fileName !== undefined && !isSafeStagedName(fileName)) {
    throw new TriageError(400, `invalid staged file name: ${fileName}`);
  }
}

/** Error with an HTTP status the route layer maps straight through. */
export class TriageError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "TriageError";
    this.status = status;
  }
}

/**
 * List every staged batch on disk with its parsed cards. Batches with
 * zero remaining .md files are skipped (they are mid-finalize or
 * empty leftovers).
 */
export function listTriage(): TriageBatch[] {
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(STAGING_ROOT(), { withFileTypes: true });
  } catch {
    return [];
  }
  const batches: TriageBatch[] = [];
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    const batchId = e.name;
    if (batchIsPlanning(batchId)) continue;
    const cards = readStagedCards(batchId);
    if (cards.length === 0) continue;
    batches.push({
      batchId,
      story: readBatchStory(batchId),
      cards,
    });
  }
  batches.sort((a, b) => a.batchId.localeCompare(b.batchId));
  return batches;
}

function readStagedCards(batchId: string): TriageCard[] {
  const dir = stagingDirFor(batchId);
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const cards: TriageCard[] = [];
  for (const e of entries) {
    if (!e.isFile() || !e.name.endsWith(".md")) continue;
    let raw: string;
    try {
      raw = fs.readFileSync(path.join(dir, e.name), "utf8");
    } catch {
      continue;
    }
    const { frontmatter, body } = parseFrontmatter(raw);
    const fmId = frontmatter["id"];
    const fmTitle = frontmatter["title"];
    const id =
      typeof fmId === "string" && fmId.length > 0
        ? fmId
        : path.basename(e.name, ".md");
    cards.push({
      id,
      title:
        typeof fmTitle === "string" && fmTitle.length > 0 ? fmTitle : id,
      file: e.name,
      bodyExcerpt: body.trim().slice(0, EXCERPT_CHARS),
      tier: pickNumber(frontmatter, "points"),
      model: pickString(frontmatter, "model"),
      estimatedTokens: pickNumber(frontmatter, "estimated_tokens"),
      dependsOn: pickStringArray(frontmatter, "depends_on"),
    });
  }
  cards.sort((a, b) => a.id.localeCompare(b.id));
  return cards;
}

function readBatchStory(batchId: string): string | null {
  const manifestPath = path.join(stagingDirFor(batchId), "manifest.json");
  try {
    const parsed = JSON.parse(
      fs.readFileSync(manifestPath, "utf8")
    ) as unknown;
    if (parsed !== null && typeof parsed === "object") {
      const story = (parsed as Record<string, unknown>)["story"];
      if (typeof story === "string" && story.length > 0) {
        return story.slice(0, 200);
      }
    }
  } catch {
    /* no manifest or unparseable; the inbox shows the batch anyway */
  }
  return null;
}

/** Resolve and existence-check one staged card file. */
function stagedFilePath(batchId: string, fileName: string): string {
  assertSafe(batchId, fileName);
  if (batchIsPlanning(batchId)) {
    throw new TriageError(
      409,
      `batch ${batchId} is still being planned; try again when it lands`
    );
  }
  const file = path.join(stagingDirFor(batchId), fileName);
  if (!fs.existsSync(file)) {
    throw new TriageError(
      404,
      `no staged card ${fileName} in batch ${batchId}`
    );
  }
  return file;
}

/**
 * Promote one staged card into `backlog/`. Returns the card id parsed
 * from the file (the route appends its rank and the chokidar watcher
 * announces the arrival). Refuses to overwrite an existing backlog
 * file, mirroring the batch promoter.
 */
export function promoteTriageCard(
  batchId: string,
  fileName: string
): { id: string; file: string } {
  const src = stagedFilePath(batchId, fileName);
  const target = backlogDir();
  fs.mkdirSync(target, { recursive: true });
  const dest = path.join(target, fileName);
  if (fs.existsSync(dest)) {
    throw new TriageError(
      409,
      `Card ${fileName} already exists in backlog. Refusing to overwrite.`
    );
  }
  const raw = fs.readFileSync(src, "utf8");
  const { frontmatter } = parseFrontmatter(raw);
  const fmId = frontmatter["id"];
  const id =
    typeof fmId === "string" && fmId.length > 0
      ? fmId
      : path.basename(fileName, ".md");
  fs.renameSync(src, dest);
  finalizeBatchIfDrained(batchId);
  return { id, file: dest };
}

/**
 * Decline one staged card: park it under `_declined/<batchId>/`.
 * Non-destructive; nothing the planner produced is erased.
 */
export function declineTriageCard(batchId: string, fileName: string): void {
  const src = stagedFilePath(batchId, fileName);
  const dir = declinedDirFor(batchId);
  fs.mkdirSync(dir, { recursive: true });
  // fs.rename silently replaces an existing destination on both POSIX
  // and Windows. A previously-declined copy (e.g. the file was hand-
  // resurrected into staging and declined again) must not be
  // overwritten, so collide into a numbered sibling instead.
  let dest = path.join(dir, fileName);
  for (let n = 1; fs.existsSync(dest); n++) {
    dest = path.join(
      dir,
      `${path.basename(fileName, ".md")}.${n}.md`
    );
  }
  fs.renameSync(src, dest);
  finalizeBatchIfDrained(batchId);
}

/**
 * Read the staged card a merge consumes: id, title, and full body.
 * The route appends this to the target card (through the card store,
 * which owns card-file writes) and then declines the staged file.
 */
export function readStagedCardForMerge(
  batchId: string,
  fileName: string
): { id: string; title: string; body: string } {
  const src = stagedFilePath(batchId, fileName);
  const raw = fs.readFileSync(src, "utf8");
  const { frontmatter, body } = parseFrontmatter(raw);
  const fmId = frontmatter["id"];
  const fmTitle = frontmatter["title"];
  const id =
    typeof fmId === "string" && fmId.length > 0
      ? fmId
      : path.basename(fileName, ".md");
  return {
    id,
    title:
      typeof fmTitle === "string" && fmTitle.length > 0 ? fmTitle : id,
    body: body.trim(),
  };
}

/**
 * Once a batch has no staged .md left, archive its manifest to
 * `_batches/<batchId>/` (same place the batch promoter puts it) and
 * remove the staging dir, so the inbox and `stories/pending` don't
 * show husks.
 */
function finalizeBatchIfDrained(batchId: string): void {
  const dir = stagingDirFor(batchId);
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  const remaining = entries.filter(
    (e) => e.isFile() && e.name.endsWith(".md")
  );
  if (remaining.length > 0) return;

  const manifestSrc = path.join(dir, "manifest.json");
  if (fs.existsSync(manifestSrc)) {
    const batchesTarget = batchesDirFor(batchId);
    fs.mkdirSync(batchesTarget, { recursive: true });
    try {
      fs.renameSync(
        manifestSrc,
        path.join(batchesTarget, "manifest.json")
      );
    } catch {
      // Archive failed (e.g. an AV/indexer hold on Windows). KEEP the
      // staging dir -- removing it here would delete the manifest the
      // rename just failed to move. The card-less dir lingers with the
      // manifest preserved (the inbox hides it); an operator can
      // finish the archive by hand.
      return;
    }
  }
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* swallow; a leftover empty dir is harmless */
  }
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
