/**
 * Card store backed by the filesystem. Reads every `.md` file under the
 * status subfolders of CARDS_DIR, holds them in memory by id, and watches
 * the tree with chokidar so the in-memory index stays in sync with disk.
 *
 * Every change publishes a BoardEvent so the SSE route can push to
 * connected dashboards.
 *
 * Move semantics: a card "move" is a rename of the file from one status
 * folder to another *plus* a rewrite of the `status:` line. We do the
 * rewrite first, then the rename, then publish. If the rename fails the
 * rewrite is reverted. The chokidar watcher will also fire its own
 * add/unlink events when the rename lands; we tolerate the duplicate by
 * making the in-memory update idempotent.
 */

import chokidar from "chokidar";
import fs from "node:fs";
import path from "node:path";

import { config } from "../config.js";
import { log } from "../logger.js";
import { publish } from "../events/bus.js";
import { deriveEvents } from "../events/derive.js";
import { appendEvent, countEventsForCard } from "../db/events.js";
import { parseFrontmatter, rewriteField, rewriteStatus } from "./frontmatter.js";

/**
 * Canonical status values, in the order they should render as columns.
 * Each maps to a single folder under CARDS_DIR. Cards whose `status:`
 * field doesn't match the folder they're in are tolerated; we trust the
 * folder, since the folder is what the runner moves.
 */
export const STATUSES = [
  { id: "backlog", folder: "backlog", label: "Backlog" },
  { id: "active", folder: "active", label: "Active" },
  {
    id: "awaiting_amendment_review",
    folder: "amendments",
    label: "In Review",
  },
  { id: "done", folder: "done", label: "Done" },
  { id: "blocked", folder: "blocked", label: "Blocked" },
] as const;

export type StatusId = (typeof STATUSES)[number]["id"];

const FOLDER_BY_STATUS: Record<StatusId, string> = STATUSES.reduce(
  (acc, s) => {
    acc[s.id] = s.folder;
    return acc;
  },
  {} as Record<StatusId, string>
);

const STATUS_BY_FOLDER: Record<string, StatusId> = STATUSES.reduce(
  (acc, s) => {
    acc[s.folder] = s.id;
    return acc;
  },
  {} as Record<string, StatusId>
);

export interface Card {
  readonly id: string;
  readonly file: string; // absolute path
  readonly status: StatusId;
  readonly frontmatter: Record<string, unknown>;
  readonly body: string;
  readonly raw: string;
  readonly mtimeMs: number;
}

const index = new Map<string, Card>();
const fileToId = new Map<string, string>();

export function listCards(): Card[] {
  return Array.from(index.values()).sort((a, b) => {
    // Stable sort: status order, then id.
    const sa = statusRank(a.status);
    const sb = statusRank(b.status);
    if (sa !== sb) return sa - sb;
    return a.id.localeCompare(b.id);
  });
}

export function getCard(id: string): Card | undefined {
  return index.get(id);
}

export function getColumns(): ReadonlyArray<{
  readonly id: StatusId;
  readonly label: string;
}> {
  return STATUSES.map((s) => ({ id: s.id, label: s.label }));
}

function statusRank(s: StatusId): number {
  return STATUSES.findIndex((x) => x.id === s);
}

function statusFolderFor(s: StatusId): string {
  return FOLDER_BY_STATUS[s];
}

function detectStatusFromPath(file: string): StatusId | null {
  const rel = path.relative(config.cardsDir, file);
  const parts = rel.split(/[\\/]/);
  const folder = parts[0];
  if (!folder) return null;
  return STATUS_BY_FOLDER[folder] ?? null;
}

function readCardFromDisk(file: string): Card | null {
  let raw: string;
  let mtimeMs: number;
  try {
    raw = fs.readFileSync(file, "utf8");
    mtimeMs = fs.statSync(file).mtimeMs;
  } catch (err) {
    log.warn("could not read card", { file, err: String(err) });
    return null;
  }

  const status = detectStatusFromPath(file);
  if (!status) return null;

  const { frontmatter, body } = parseFrontmatter(raw);
  const fileBase = path.basename(file, ".md");
  const fmId = typeof frontmatter["id"] === "string" ? (frontmatter["id"] as string) : null;
  const id = fmId && fmId.length > 0 ? fmId : fileBase;

  return { id, file, status, frontmatter, body, raw, mtimeMs };
}

function upsert(file: string): Card | null {
  const card = readCardFromDisk(file);
  if (!card) return null;

  // If a different file used to claim this id, drop the old mapping.
  const previousFile = [...fileToId.entries()].find(
    ([, id]) => id === card.id && [...fileToId.entries()].some(([f]) => f !== file && f !== card.file)
  );
  if (previousFile) {
    fileToId.delete(previousFile[0]);
  }

  const prev = index.get(card.id) ?? null;
  index.set(card.id, card);
  fileToId.set(file, card.id);

  publish({
    type: prev ? "card-updated" : "card-added",
    cardId: card.id,
    status: card.status,
  });
  recordDerivedEvents(prev, card);
  return card;
}

/**
 * Derive lifecycle events from the diff, persist them, and publish each
 * one on the SSE bus so the open card-modal timelines update live.
 * Errors are logged and swallowed -- a single bad row should never take
 * down the watcher loop.
 */
function recordDerivedEvents(prev: Card | null, current: Card): void {
  try {
    const derived = deriveEvents(
      prev
        ? {
            id: prev.id,
            status: prev.status,
            frontmatter: prev.frontmatter,
            mtimeMs: prev.mtimeMs,
          }
        : null,
      {
        id: current.id,
        status: current.status,
        frontmatter: current.frontmatter,
        mtimeMs: current.mtimeMs,
      }
    );
    for (const d of derived) {
      const row = appendEvent({
        cardId: d.cardId,
        type: d.type,
        at: d.at,
        details: d.details,
      });
      publish({
        type: "card-event-added",
        cardId: row.cardId,
        event: {
          id: row.id,
          cardId: row.cardId,
          type: row.type,
          at: row.at,
          details: row.details,
        },
      });
    }
  } catch (err) {
    log.warn("derive/persist card event failed", {
      cardId: current.id,
      err: String(err),
    });
  }
}

function remove(file: string): void {
  const id = fileToId.get(file);
  if (!id) return;
  fileToId.delete(file);
  index.delete(id);
  publish({ type: "card-removed", cardId: id });
}

function ensureStatusDirs(): void {
  for (const s of STATUSES) {
    const dir = path.join(config.cardsDir, s.folder);
    fs.mkdirSync(dir, { recursive: true });
  }
}

/**
 * Walk the tree once at boot to populate the index synchronously, then
 * hand off to chokidar for live updates. We do the boot walk
 * synchronously so the first HTTP request after startup gets a
 * fully-populated index instead of an empty one that fills in
 * milliseconds later.
 */
function bootstrap(): void {
  ensureStatusDirs();
  for (const s of STATUSES) {
    const dir = path.join(config.cardsDir, s.folder);
    let entries: fs.Dirent[] = [];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      if (!e.isFile() || !e.name.endsWith(".md")) continue;
      const file = path.join(dir, e.name);
      const card = readCardFromDisk(file);
      if (!card) continue;
      index.set(card.id, card);
      fileToId.set(file, card.id);
    }
  }
  // First-ever bootstrap: cards that have lived on disk without the
  // dashboard observing them get a synthesized `discovered` row so the
  // timeline always has a starting point. countEventsForCard makes this
  // idempotent across server restarts.
  for (const card of index.values()) {
    try {
      if (countEventsForCard(card.id) > 0) continue;
      appendEvent({
        cardId: card.id,
        type: "discovered",
        at: new Date(card.mtimeMs).toISOString(),
        details: { status: card.status, source: "bootstrap" },
      });
    } catch (err) {
      log.warn("bootstrap event backfill failed", {
        cardId: card.id,
        err: String(err),
      });
    }
  }
  log.info("card index ready", { count: index.size });
}

export function startWatcher(): chokidar.FSWatcher {
  bootstrap();

  const watchGlobs = STATUSES.map((s) =>
    path.join(config.cardsDir, s.folder, "*.md")
  );

  const watcher = chokidar.watch(watchGlobs, {
    ignoreInitial: true, // we did the initial walk above
    awaitWriteFinish: { stabilityThreshold: 150, pollInterval: 50 },
    persistent: true,
  });

  watcher.on("add", (file) => {
    upsert(file);
  });
  watcher.on("change", (file) => {
    upsert(file);
  });
  watcher.on("unlink", (file) => {
    remove(file);
  });
  watcher.on("error", (err) => {
    log.error("chokidar error", { err: String(err) });
  });

  return watcher;
}

/**
 * Move a card to a new status. This is the atomic operation backing the
 * drag-drop API:
 *   1. Compute target path.
 *   2. Rewrite the status line in the raw text.
 *   3. fs.rename to the new folder. fs.rename is atomic within a
 *      filesystem, which is what we have here.
 *   4. Update the in-memory index and publish.
 *
 * If the rename fails, we leave nothing modified. If we got far enough to
 * have written the rewritten content into the old location (the standard
 * rename flow does not require this; we use rename directly on the
 * original then a write), we roll back.
 */
export function moveCard(id: string, newStatus: StatusId): Card {
  const card = index.get(id);
  if (!card) throw new Error(`No card with id=${id}`);
  if (card.status === newStatus) return card;

  const folder = statusFolderFor(newStatus);
  const newFile = path.join(config.cardsDir, folder, path.basename(card.file));

  if (fs.existsSync(newFile) && newFile !== card.file) {
    throw new Error(
      `Target path already exists: ${newFile}. Refusing to overwrite.`
    );
  }

  const newRaw = rewriteStatus(card.raw, newStatus);
  // Write to a sibling temp file in the same dir as the original, then
  // rename to the new folder. Two-step is safer than truncating in place.
  const tmpFile = `${card.file}.${process.pid}.tmp`;
  fs.writeFileSync(tmpFile, newRaw, "utf8");

  try {
    fs.renameSync(tmpFile, newFile);
  } catch (err) {
    // Cleanup temp if rename fails.
    try {
      fs.unlinkSync(tmpFile);
    } catch {
      /* swallow */
    }
    throw err;
  }

  // Remove the original only if newFile differs from card.file (it should).
  if (newFile !== card.file) {
    try {
      fs.unlinkSync(card.file);
    } catch (err) {
      // We've already created the new file. Log loudly; the dashboard
      // will still see both via chokidar and we'll reconcile.
      log.warn("could not remove original card file after move", {
        file: card.file,
        err: String(err),
      });
    }
  }

  fileToId.delete(card.file);

  const moved = readCardFromDisk(newFile);
  if (!moved) {
    throw new Error(`Move succeeded but reread failed for ${newFile}`);
  }
  index.set(moved.id, moved);
  fileToId.set(newFile, moved.id);

  publish({
    type: "card-state-changed",
    cardId: moved.id,
    status: moved.status,
  });
  return moved;
}

/**
 * Patch a whitelisted set of scalar frontmatter fields on a card without
 * moving it between folders. Same atomic-write discipline as `moveCard`:
 * apply every rewrite in memory, write to a sibling temp file, rename
 * over the original.
 *
 * The route layer is responsible for validating the patch shape; this
 * function trusts that values are already coerced to the right scalar
 * type (string | number | boolean | null). `null` removes a field.
 *
 * Returns the freshly re-read card. Publishes a `card-updated` event;
 * the existing event-derive pipeline runs idempotently on every upsert
 * so any lifecycle event implied by the change surfaces automatically.
 *
 * No-op fast path: if every rewrite leaves the raw text unchanged, the
 * disk is not touched and the in-memory card is returned as-is.
 */
export type FrontmatterScalar = string | number | boolean | null;

export function patchCardFrontmatter(
  id: string,
  patch: Record<string, FrontmatterScalar>
): Card {
  const card = index.get(id);
  if (!card) throw new Error(`No card with id=${id}`);

  let next = card.raw;
  for (const [key, value] of Object.entries(patch)) {
    next = rewriteField(next, key, value);
  }
  if (next === card.raw) return card;

  const tmpFile = `${card.file}.${process.pid}.tmp`;
  fs.writeFileSync(tmpFile, next, "utf8");
  try {
    fs.renameSync(tmpFile, card.file);
  } catch (err) {
    try {
      fs.unlinkSync(tmpFile);
    } catch {
      /* swallow */
    }
    throw err;
  }

  const updated = readCardFromDisk(card.file);
  if (!updated) {
    throw new Error(`Patch succeeded but reread failed for ${card.file}`);
  }
  index.set(updated.id, updated);
  fileToId.set(card.file, updated.id);

  publish({
    type: "card-updated",
    cardId: updated.id,
    status: updated.status,
  });
  recordDerivedEvents(card, updated);
  return updated;
}
