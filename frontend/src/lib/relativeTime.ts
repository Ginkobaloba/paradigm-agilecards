/**
 * Compact relative-time formatter for tiles. We format the delta
 * ourselves rather than leaning on date-fns because:
 *   - the tile is dense; we want "2h" not "about 2 hours"
 *   - we want a "stale" prefix once a working card hasn't moved in a
 *     while, so it stands out to the operator
 *   - the unit tests need to inject `now`, which date-fns' `*ToNow*`
 *     helpers don't honor (they always read Date.now())
 *
 * Output forms: "now" / "5s" / "12m" / "2h" / "3d" / "5w" / "4mo" / "2y".
 */

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

/**
 * Threshold (ms) past which a non-done card counts as "stale". Default
 * three days -- matches the roadmap's mention of "stale 4d" being the
 * kind of signal we want without being annoyingly chatty.
 */
const STALE_AFTER_MS = 3 * DAY;

export interface RelativeTime {
  /** Compact label: "now", "5s", "12m", "2h", "3d", "5w", "4mo", "2y". */
  label: string;
  /** True if the card is on a working column (not done) and is stale. */
  stale: boolean;
}

/**
 * Build a compact relative-time view. `mtimeMs` is the file-system mtime
 * the backend exposes on every CardSummary. `isStaleEligible` controls
 * whether the "stale" flag may be set; pass false for done/backlog cards
 * where staleness is uninteresting.
 *
 * Returns null if the timestamp is missing or in the future (clock skew).
 */
export function relativeTime(
  mtimeMs: number | null | undefined,
  options: { isStaleEligible?: boolean; now?: number } = {}
): RelativeTime | null {
  if (typeof mtimeMs !== "number" || !Number.isFinite(mtimeMs)) return null;
  const now = options.now ?? Date.now();
  const delta = now - mtimeMs;
  if (delta < 0) return null;

  const label = formatDelta(delta);
  const stale = options.isStaleEligible === true && delta >= STALE_AFTER_MS;
  return { label, stale };
}

function formatDelta(deltaMs: number): string {
  if (deltaMs < SECOND) return "now";
  if (deltaMs < MINUTE) return `${Math.floor(deltaMs / SECOND)}s`;
  if (deltaMs < HOUR) return `${Math.floor(deltaMs / MINUTE)}m`;
  if (deltaMs < DAY) return `${Math.floor(deltaMs / HOUR)}h`;
  if (deltaMs < WEEK) return `${Math.floor(deltaMs / DAY)}d`;
  if (deltaMs < MONTH) return `${Math.floor(deltaMs / WEEK)}w`;
  if (deltaMs < YEAR) return `${Math.floor(deltaMs / MONTH)}mo`;
  return `${Math.floor(deltaMs / YEAR)}y`;
}
