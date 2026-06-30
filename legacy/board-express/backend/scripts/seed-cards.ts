/**
 * Dev seed. Populates a card store with realistic sample cards so the
 * dashboard renders a believable working board instead of five empty
 * columns. Handy for demos, screenshots, and frontend work without a
 * live runner attached.
 *
 *   npm run seed -- --dir C:\dev\todo-sample
 *   CARDS_DIR=C:\dev\todo-sample npm run seed
 *
 * The script clears the five status folders of their *.md files and
 * rewrites the sample set, so it is safe to re-run. It refuses to touch
 * a directory whose basename is "todo" unless --force is passed, so a
 * stray run can't clobber a real card store.
 *
 * This never writes to the live backlog used by a runner; point --dir at
 * a throwaway directory.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import yaml from "js-yaml";

type Status =
  | "backlog"
  | "active"
  | "awaiting_amendment_review"
  | "done"
  | "blocked";

/** status value -> on-disk folder. Mirrors backend/src/fs/cards.ts. */
const FOLDER: Record<Status, string> = {
  backlog: "backlog",
  active: "active",
  awaiting_amendment_review: "amendments",
  done: "done",
  blocked: "blocked",
};

interface CardSpec {
  readonly id: string;
  readonly title: string;
  readonly project: string;
  readonly status: Status;
  readonly points: number;
  readonly stakes: "low" | "medium" | "high";
  readonly difficulty: "shallow" | "deep";
  readonly model: string;
  readonly extendedThinking: boolean;
  readonly modelFloor: "haiku" | "sonnet" | "opus";
  readonly batch: string;
  readonly estimatedTokens: number;
  readonly estimatedMinutes: number;
  readonly created: string;
  readonly sizingNote: string;
  readonly context: string;
  readonly scope: readonly string[];
  readonly outOfScope: readonly string[];
  readonly acceptance: readonly string[];
  readonly touches: readonly string[];
  readonly pinRequired?: boolean;
  readonly dependsOn?: readonly string[];
  readonly pointers?: readonly string[];
  // execution / lifecycle state
  readonly claimedBy?: string;
  readonly startedAt?: string;
  readonly finishedAt?: string;
  readonly modelUsed?: string;
  readonly actualTokens?: number;
  readonly actualMinutes?: number;
  readonly lastHeartbeat?: string;
  readonly mergeStatus?: string;
  readonly verifiedAt?: string;
  readonly verifiedBy?: string;
  readonly verifierSkippedReason?: string;
  readonly changeRequest?: string;
}

function uuid(): string {
  return crypto.randomUUID();
}

function storyHash(seed: string): string {
  return crypto.createHash("sha256").update(seed, "utf8").digest("hex");
}

/**
 * Build the frontmatter object in canonical key order. js-yaml preserves
 * object insertion order on dump, so this controls the on-disk layout.
 */
function frontmatter(c: CardSpec): Record<string, unknown> {
  const done = c.status === "done";
  return {
    verifier_schema_version: "1.3",
    id: c.id,
    title: c.title,
    project: c.project,
    status: c.status,
    points: c.points,
    stakes: c.stakes,
    difficulty: c.difficulty,
    thinking_depth: c.extendedThinking ? "deep" : "shallow",
    model: c.model,
    extended_thinking: c.extendedThinking,
    model_floor: c.modelFloor,
    pin_required: c.pinRequired ?? false,
    requires_pre_approval: c.stakes === "high",
    cost_cap_usd: null,
    estimated_tokens: c.estimatedTokens,
    actual_tokens: c.actualTokens ?? null,
    estimated_duration_minutes: c.estimatedMinutes,
    actual_duration_minutes: c.actualMinutes ?? null,
    trace_id: uuid(),
    sizing_note: c.sizingNote,
    depends_on: c.dependsOn ?? [],
    touches: c.touches,
    batch: c.batch,
    story_hash: storyHash(c.id + c.title),
    created: c.created,
    started_at: c.startedAt ?? null,
    finished_at: c.finishedAt ?? null,
    claimed_by: c.claimedBy ?? null,
    model_used: c.modelUsed ?? (done ? c.model : null),
    last_heartbeat: c.lastHeartbeat ?? null,
    branch: `card/${c.id}`,
    base_branch: "main",
    merge_status: c.mergeStatus ?? (done ? "merged" : "pending"),
    verified_at: c.verifiedAt ?? null,
    verified_by: c.verifiedBy ?? null,
    verifier_skipped_reason: c.verifierSkippedReason ?? null,
    cascade_history: [],
    verifier_cascade_history: [],
    standup_reason: null,
  };
}

function bulletList(items: readonly string[]): string {
  return items.map((s) => `- ${s}`).join("\n");
}

function renderBody(c: CardSpec): string {
  const sections: string[] = [
    `## Context\n\n${c.context}`,
    `## Scope\n\n${bulletList(c.scope)}`,
    `## Out of scope\n\n${bulletList(c.outOfScope)}`,
    `## Acceptance criteria\n\n${bulletList(c.acceptance)}`,
  ];
  if (c.changeRequest) {
    sections.push(`## Change request\n\n${c.changeRequest}`);
  }
  if (c.pointers && c.pointers.length > 0) {
    sections.push(`## Pointers\n\n${bulletList(c.pointers)}`);
  }
  return sections.join("\n\n");
}

function renderCard(c: CardSpec): string {
  const fm = yaml.dump(frontmatter(c), { lineWidth: -1, noRefs: true });
  return `---\n${fm}---\n\n${renderBody(c)}\n`;
}

function parseArgs(argv: string[]): { dir: string; force: boolean } {
  let dir = process.env["CARDS_DIR"] ?? "";
  let force = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dir" && i + 1 < argv.length) {
      dir = String(argv[i + 1]);
      i += 1;
    } else if (a === "--force") {
      force = true;
    }
  }
  return { dir, force };
}

function main(): void {
  const { dir, force } = parseArgs(process.argv.slice(2));
  if (!dir) {
    process.stderr.write(
      'seed: pass --dir <path> or set CARDS_DIR. Point it at a throwaway\n' +
        "directory, never a live runner's card store.\n"
    );
    process.exit(2);
  }
  const resolved = path.resolve(dir);
  if (path.basename(resolved).toLowerCase() === "todo" && !force) {
    process.stderr.write(
      `seed: refusing to seed ${resolved} -- its name looks like a real\n` +
        "card store. Re-run with --force if you really mean it.\n"
    );
    process.exit(2);
  }

  let written = 0;
  const counts: Record<Status, number> = {
    backlog: 0,
    active: 0,
    awaiting_amendment_review: 0,
    done: 0,
    blocked: 0,
  };

  for (const status of Object.keys(FOLDER) as Status[]) {
    const folder = path.join(resolved, FOLDER[status]);
    fs.mkdirSync(folder, { recursive: true });
    // Clear existing *.md so re-runs stay clean.
    for (const entry of fs.readdirSync(folder)) {
      if (entry.endsWith(".md")) fs.rmSync(path.join(folder, entry));
    }
  }

  for (const card of CARDS) {
    const folder = path.join(resolved, FOLDER[card.status]);
    const file = path.join(folder, `${card.id}.md`);
    fs.writeFileSync(file, renderCard(card), "utf8");
    written += 1;
    counts[card.status] += 1;
  }

  process.stdout.write(
    `seed: wrote ${written} cards to ${resolved}\n` +
      (Object.keys(counts) as Status[])
        .map((s) => `  ${FOLDER[s].padEnd(12)} ${counts[s]}`)
        .join("\n") +
      "\n"
  );
}

// ---------------------------------------------------------------------------
// Sample data. A believable two-project, multi-batch slice of work.
// ---------------------------------------------------------------------------

const CARDS: readonly CardSpec[] = [
  {
    id: "b040-01-bootstrap-express-server",
    title: "Bootstrap the Express server and health check",
    project: "C:\\dev\\agile-cards-board",
    status: "done",
    points: 2,
    stakes: "low",
    difficulty: "shallow",
    model: "claude-haiku-4-5",
    extendedThinking: false,
    modelFloor: "haiku",
    batch: "b040",
    estimatedTokens: 12000,
    estimatedMinutes: 15,
    actualTokens: 10840,
    actualMinutes: 13,
    created: "2026-05-08",
    startedAt: "2026-05-08T15:02:00Z",
    finishedAt: "2026-05-08T15:15:00Z",
    verifiedAt: "2026-05-08T15:16:00Z",
    verifiedBy: "cold-read-verifier (claude-haiku-4-5)",
    sizingNote: "low stakes, shallow -- tier 2, haiku without thinking",
    context:
      "The dashboard backend needs an entry point before anything else can " +
      "land. This card stands up the Express app, the config module, and an " +
      "unauthenticated health endpoint the Cloudflare tunnel can poll.",
    scope: [
      "Add `src/server.ts` that builds the Express app and listens on `PORT`.",
      "Add `src/config.ts` as the single source of truth for env parsing.",
      "Expose `GET /healthz` returning `{ ok, cardsDir, version }`, no auth.",
      "Wire a small JSON access log so every request prints method/status/ms.",
    ],
    outOfScope: [
      "Auth middleware -- b040-03 owns the token scheme.",
      "Any card routes -- those land once the watcher exists (b040-02).",
    ],
    acceptance: [
      "`npm run dev` boots the server and logs a `listening` line.",
      "`GET /healthz` returns 200 with the configured `cardsDir`.",
      "`npm run typecheck` is clean.",
    ],
    touches: ["backend/src/server.ts", "backend/src/config.ts"],
  },
  {
    id: "b040-02-build-card-watcher",
    title: "Build the chokidar card-file watcher",
    project: "C:\\dev\\agile-cards-board",
    status: "done",
    points: 4,
    stakes: "medium",
    difficulty: "deep",
    model: "claude-sonnet-4-6",
    extendedThinking: true,
    modelFloor: "sonnet",
    batch: "b040",
    estimatedTokens: 46000,
    estimatedMinutes: 55,
    actualTokens: 51200,
    actualMinutes: 62,
    created: "2026-05-09",
    startedAt: "2026-05-09T17:20:00Z",
    finishedAt: "2026-05-09T18:22:00Z",
    verifiedAt: "2026-05-09T18:25:00Z",
    verifiedBy: "cold-read-verifier (claude-sonnet-4-6)",
    sizingNote:
      "medium stakes (read-only index) + deep (rename/move race handling) -- tier 4",
    context:
      "Cards live as markdown files on disk under per-status folders. The " +
      "backend needs an in-memory index that stays in sync as a runner adds, " +
      "edits, and moves cards. Move is the hard part: it is a status rewrite " +
      "plus a cross-folder rename, and chokidar fires its own add/unlink.",
    scope: [
      "Walk the status folders once at boot to populate the index synchronously.",
      "Watch each `*.md` glob with chokidar and keep the index live.",
      "Make upsert/remove idempotent so a rename's duplicate events are safe.",
      "Publish a `BoardEvent` on every change for the SSE route to fan out.",
    ],
    outOfScope: [
      "The SSE route itself -- b041-03 consumes the event bus.",
      "Persisting the index to SQLite. Disk stays the source of truth.",
    ],
    acceptance: [
      "Adding a file under `backlog/` shows the card in the index within 200ms.",
      "A cross-folder move updates status without dropping or duplicating the card.",
      "The boot walk leaves the first `/api/cards` call fully populated.",
    ],
    touches: ["backend/src/fs/cards.ts", "backend/src/events/bus.ts"],
    pointers: [
      "chokidar `awaitWriteFinish` smooths partial writes from slow editors.",
      "`fs.rename` is atomic within a filesystem -- lean on it for the move.",
    ],
  },
  {
    id: "b040-03-bearer-token-auth",
    title: "Implement bearer-token auth middleware",
    project: "C:\\dev\\agile-cards-board",
    status: "done",
    points: 4,
    stakes: "high",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    pinRequired: true,
    batch: "b040",
    estimatedTokens: 38000,
    estimatedMinutes: 45,
    actualTokens: 35600,
    actualMinutes: 41,
    created: "2026-05-10",
    startedAt: "2026-05-10T14:05:00Z",
    finishedAt: "2026-05-10T14:46:00Z",
    verifiedAt: "2026-05-10T14:50:00Z",
    verifiedBy: "cold-read-verifier (claude-sonnet-4-6)",
    sizingNote:
      "high stakes (auth surface) but a well-trodden pattern -- tier 4, pinned",
    context:
      "Every `/api/*` route and the SSE stream must be gated. Tokens are " +
      "minted on the backend, stored as SHA-256 hashes with a public label, " +
      "and shown in plaintext exactly once at creation.",
    scope: [
      "Add a `tokens` table and a hash-only token store.",
      "Add `requireAuth` middleware reading `Authorization: Bearer` or `?token`.",
      "Ship `create-token` / `list-tokens` / `revoke-token` CLI scripts.",
      "Touch `last_used_at` on every successful validation.",
    ],
    outOfScope: [
      "Per-tenant token isolation -- tracked separately in b044-03.",
      "OAuth or any third-party identity provider.",
    ],
    acceptance: [
      "A request with no token gets 401; a minted token gets 200.",
      "The database stores only the hash -- plaintext never persists.",
      "SSE accepts the token via query param since EventSource can't set headers.",
    ],
    touches: [
      "backend/src/routes/auth.ts",
      "backend/src/auth/tokens.ts",
      "backend/src/auth/hash.ts",
    ],
  },
  {
    id: "b041-01-kanban-drag-drop",
    title: "Wire kanban drag-and-drop with dnd-kit",
    project: "C:\\dev\\agile-cards-board",
    status: "done",
    points: 3,
    stakes: "medium",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    batch: "b041",
    estimatedTokens: 24000,
    estimatedMinutes: 30,
    actualTokens: 26900,
    actualMinutes: 34,
    created: "2026-05-11",
    startedAt: "2026-05-11T16:10:00Z",
    finishedAt: "2026-05-11T16:44:00Z",
    verifiedAt: "2026-05-11T16:47:00Z",
    verifiedBy: "cold-read-verifier (claude-haiku-4-5)",
    sizingNote: "medium stakes, shallow -- tier 3, sonnet without thinking",
    context:
      "The board's whole point is moving cards between columns. This card " +
      "wires @dnd-kit so a drag from one column to another calls the move " +
      "API, with an optimistic UI update that the SSE echo reconciles.",
    scope: [
      "Make each column a dnd-kit droppable and each tile a sortable.",
      "On drop, optimistically move the card in the store, then call the API.",
      "Roll back to the canonical card on a failed move.",
      "Use a 6px activation distance so a click still opens the modal.",
    ],
    outOfScope: [
      "Reordering within a column -- columns sort by id for now.",
      "Multi-select drag. One card at a time.",
    ],
    acceptance: [
      "Dragging a card across columns persists the new status on disk.",
      "A rejected move snaps the card back and surfaces the error.",
      "A quick click still opens the detail modal.",
    ],
    touches: [
      "frontend/src/routes/Kanban.tsx",
      "frontend/src/components/Column.tsx",
      "frontend/src/components/CardTile.tsx",
    ],
  },
  {
    id: "b041-02-card-detail-modal",
    title: "Ship the card-detail modal",
    project: "C:\\dev\\agile-cards-board",
    status: "done",
    points: 2,
    stakes: "low",
    difficulty: "shallow",
    model: "claude-haiku-4-5",
    extendedThinking: false,
    modelFloor: "haiku",
    batch: "b041",
    estimatedTokens: 13000,
    estimatedMinutes: 16,
    actualTokens: 12100,
    actualMinutes: 14,
    created: "2026-05-11",
    startedAt: "2026-05-11T18:00:00Z",
    finishedAt: "2026-05-11T18:14:00Z",
    verifierSkippedReason:
      "cascade-clean run: high-confidence executor pass, no AC items routed to subjective evaluation",
    sizingNote: "low stakes, shallow -- tier 2, haiku without thinking",
    context:
      "A tile shows a summary; the operator needs the full card. This card " +
      "adds a Radix dialog that lazy-loads the body and renders the " +
      "frontmatter as a readable table.",
    scope: [
      "Open a Radix dialog when a tile is clicked.",
      "Lazy-load the card body via `GET /api/cards/:id`.",
      "Render the frontmatter as a key/value table and the body as markdown.",
    ],
    outOfScope: [
      "Editing the card from the modal -- read-only for v0+.",
      "Inline verifier-history rendering. The raw fields are enough for now.",
    ],
    acceptance: [
      "Clicking a tile opens the modal with the card's title.",
      "The body renders GitHub-flavored markdown.",
      "Escape and the close button both dismiss the dialog.",
    ],
    touches: ["frontend/src/components/CardModal.tsx"],
  },
  {
    id: "b041-03-sse-live-updates",
    title: "Stream live card updates over SSE",
    project: "C:\\dev\\agile-cards-board",
    status: "done",
    points: 4,
    stakes: "medium",
    difficulty: "deep",
    model: "claude-sonnet-4-6",
    extendedThinking: true,
    modelFloor: "sonnet",
    batch: "b041",
    estimatedTokens: 44000,
    estimatedMinutes: 52,
    actualTokens: 47300,
    actualMinutes: 58,
    created: "2026-05-12",
    startedAt: "2026-05-12T15:30:00Z",
    finishedAt: "2026-05-12T16:28:00Z",
    verifiedAt: "2026-05-12T16:32:00Z",
    verifiedBy: "cold-read-verifier (claude-sonnet-4-6)",
    sizingNote:
      "medium stakes + deep (reconnect + diff reconciliation) -- tier 4",
    context:
      "Two tabs open on the board should agree. This card streams board " +
      "events over SSE so a move in one tab, or a runner's edit on disk, " +
      "shows up everywhere within a few hundred milliseconds.",
    scope: [
      "Add `GET /events` fanning the event bus out to every connection.",
      "Send a 25s heartbeat so idle proxies keep the stream alive.",
      "Add a `useSSE` hook that patches the Zustand store per event.",
      "Refetch the canonical card on add/update instead of trusting the wire.",
    ],
    outOfScope: [
      "A reconnect-backoff banner -- b043 follow-up owns the UX around drops.",
      "WebSockets. SSE is enough for a one-way feed.",
    ],
    acceptance: [
      "A move in tab A appears in tab B without a refresh.",
      "A card file edited on disk updates the board live.",
      "The stream survives a 30s idle window behind a proxy.",
    ],
    touches: [
      "backend/src/routes/sse.ts",
      "frontend/src/hooks/useSSE.ts",
    ],
  },
  {
    id: "b042-01-runner-claim-loop",
    title: "Harden the runner claim loop against double-claims",
    project: "C:\\dev\\agile-cards",
    status: "active",
    points: 4,
    stakes: "high",
    difficulty: "deep",
    model: "claude-sonnet-4-6",
    extendedThinking: true,
    modelFloor: "sonnet",
    batch: "b042",
    estimatedTokens: 52000,
    estimatedMinutes: 60,
    created: "2026-05-14",
    startedAt: "2026-05-19T13:40:00Z",
    claimedBy: "runner-01",
    modelUsed: "claude-sonnet-4-6",
    lastHeartbeat: "2026-05-19T15:05:00Z",
    mergeStatus: "open",
    sizingNote:
      "high stakes (correctness of the claim primitive) + deep -- tier 4",
    context:
      "Two runner processes can race for the same backlog card. The claim " +
      "step has to be atomic: the loser must see the card already taken and " +
      "move on, never run the same card twice.",
    scope: [
      "Make the claim a single atomic rename from `backlog/` to `active/`.",
      "Stamp `claimed_by`, `started_at`, and `last_heartbeat` on claim.",
      "On a lost race, log and re-scan the backlog instead of erroring.",
      "Add a stress test that spawns N runners against one backlog card.",
    ],
    outOfScope: [
      "Orphan reclaim -- b042-02 owns the timeout path.",
      "Distributed runners across machines. Single-host for now.",
    ],
    acceptance: [
      "N concurrent claimers on one card produce exactly one winner.",
      "The losers continue cleanly without a crash or a duplicate run.",
      "`claimed_by` and `started_at` are set exactly once.",
    ],
    touches: ["runner/claim.py", "tests/runner/test_claim_race.py"],
  },
  {
    id: "b042-03-sigterm-safe-shutdown",
    title: "Make runner shutdown SIGTERM-safe",
    project: "C:\\dev\\agile-cards",
    status: "active",
    points: 3,
    stakes: "medium",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    batch: "b042",
    estimatedTokens: 23000,
    estimatedMinutes: 28,
    created: "2026-05-14",
    startedAt: "2026-05-19T14:25:00Z",
    claimedBy: "runner-02",
    modelUsed: "claude-sonnet-4-6",
    lastHeartbeat: "2026-05-19T15:02:00Z",
    mergeStatus: "open",
    sizingNote: "medium stakes, shallow -- tier 3, sonnet without thinking",
    context:
      "When the host stops the runner, a card mid-execution should land back " +
      "in `backlog/` cleanly rather than rot in `active/` until orphan " +
      "reclaim notices. A SIGTERM handler can do the graceful release.",
    scope: [
      "Trap SIGTERM/SIGINT and finish or release the in-flight card.",
      "Clear `claimed_by` / `started_at` when a card is released early.",
      "Flush logs and close the event stream before exit.",
    ],
    outOfScope: [
      "Checkpoint/resume of partial executor work. Release-and-rerun is fine.",
    ],
    acceptance: [
      "A SIGTERM during execution returns the card to `backlog/`.",
      "No card is left stranded in `active/` after a clean stop.",
      "Exit code is 0 on a graceful shutdown.",
    ],
    touches: ["runner/lifecycle.py", "runner/__main__.py"],
  },
  {
    id: "b042-02-orphan-reclaim-test",
    title: "Add an integration test for orphan reclaim",
    project: "C:\\dev\\agile-cards",
    status: "awaiting_amendment_review",
    points: 3,
    stakes: "medium",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    batch: "b042",
    estimatedTokens: 25000,
    estimatedMinutes: 30,
    actualTokens: 28400,
    actualMinutes: 36,
    created: "2026-05-14",
    startedAt: "2026-05-18T19:10:00Z",
    finishedAt: "2026-05-18T19:46:00Z",
    claimedBy: "runner-01",
    modelUsed: "claude-sonnet-4-6",
    mergeStatus: "requires_review",
    sizingNote: "medium stakes, shallow -- tier 3, sonnet without thinking",
    context:
      "A card stuck in `active/` with a stale heartbeat must be reclaimed to " +
      "`backlog/`. This card adds an end-to-end test that fakes a dead runner " +
      "and asserts the sweep picks the card back up.",
    scope: [
      "Add a test that writes a card to `active/` with an old `last_heartbeat`.",
      "Run the orphan sweep and assert the card returns to `backlog/`.",
      "Assert `claimed_by` / `started_at` / `last_heartbeat` are cleared.",
    ],
    outOfScope: [
      "Tuning the default `orphan_timeout_minutes`. Test uses a fixed value.",
    ],
    acceptance: [
      "The test reclaims a stale `active/` card to `backlog/`.",
      "Reclaim clears the claim fields.",
      "A fresh heartbeat is left untouched by the sweep.",
    ],
    touches: ["tests/runner/test_orphan_reclaim.py"],
    changeRequest:
      "The executor used a hard-coded 2-hour timeout in the test, which " +
      "makes the suite slow and couples it to the default config. Requesting " +
      "an amendment to inject `orphan_timeout_minutes` as a fixture so the " +
      "test runs with a 1-minute window. Reviewer should confirm the fixture " +
      "is also threaded through `test_claim_race.py` before this merges.",
  },
  {
    id: "b042-05-document-cascade-routing",
    title: "Document the cascade-on-confidence routing table",
    project: "C:\\dev\\agile-cards",
    status: "backlog",
    points: 2,
    stakes: "low",
    difficulty: "shallow",
    model: "claude-haiku-4-5",
    extendedThinking: false,
    modelFloor: "haiku",
    batch: "b042",
    estimatedTokens: 11000,
    estimatedMinutes: 14,
    created: "2026-05-15",
    sizingNote: "low stakes, shallow -- tier 2, haiku without thinking",
    context:
      "The runner escalates a card to a stronger model when executor " +
      "confidence drops below a threshold. The routing table that decides " +
      "from-tier/to-tier exists in code but is undocumented.",
    scope: [
      "Write a `docs/cascade-routing.md` explaining the from/to tier table.",
      "Document the confidence thresholds and where they are configured.",
      "Add a worked example of a tier-3 to tier-5 escalation.",
    ],
    outOfScope: [
      "Changing the routing table itself. Documentation only.",
    ],
    acceptance: [
      "`docs/cascade-routing.md` exists and covers every tier transition.",
      "The thresholds in the doc match the values in `lib/cascade.py`.",
    ],
    touches: ["docs/cascade-routing.md"],
  },
  {
    id: "b043-01-storage-substrate-v2",
    title: "Design the storage-substrate v2 abstraction",
    project: "C:\\dev\\agile-cards",
    status: "backlog",
    points: 5,
    stakes: "high",
    difficulty: "deep",
    model: "claude-opus-4-6",
    extendedThinking: true,
    modelFloor: "opus",
    pinRequired: true,
    batch: "b043",
    estimatedTokens: 92000,
    estimatedMinutes: 110,
    created: "2026-05-16",
    sizingNote:
      "high stakes (touches every read/write path) + deep -- tier 5, pinned",
    context:
      "Cards are filesystem-only today. Before adding a database backend the " +
      "runner needs a storage interface that both a filesystem store and a " +
      "future SQL store can satisfy, without leaking either's assumptions.",
    scope: [
      "Define a `CardStore` interface covering list/get/move/claim.",
      "Re-express the current filesystem store as one implementation of it.",
      "Write an ADR weighing filesystem-of-record vs database-of-record.",
      "Specify the migration path so existing `todo/` trees keep working.",
    ],
    outOfScope: [
      "Implementing the SQL store -- b043-02 builds on this design.",
      "Touching the dashboard backend. This is a runner-side abstraction.",
    ],
    acceptance: [
      "An ADR captures the decision and its trade-offs.",
      "The `CardStore` interface compiles with the filesystem store behind it.",
      "No behavior change: the existing runner suite still passes.",
    ],
    touches: ["docs/adr/0007-storage-substrate.md", "lib/store/__init__.py"],
  },
  {
    id: "b043-03-output-root-flag",
    title: "Add an --output-root flag to the cards skill",
    project: "C:\\dev\\agile-cards",
    status: "backlog",
    points: 3,
    stakes: "medium",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    batch: "b043",
    estimatedTokens: 22000,
    estimatedMinutes: 28,
    created: "2026-05-16",
    sizingNote: "medium stakes, shallow -- tier 3, sonnet without thinking",
    context:
      "The dashboard's submit-story flow redirects planner output to a " +
      "staging dir via a prompt directive, which is brittle. A first-class " +
      "`--output-root` flag would make the redirect explicit and testable.",
    scope: [
      "Add an `--output-root` flag that overrides where cards are written.",
      "Default it to the project's `todo/` tree when the flag is absent.",
      "Update the submit-story invoker to pass the flag instead of a prompt note.",
    ],
    outOfScope: [
      "Reworking the dashboard staging promotion. Only the write target moves.",
    ],
    acceptance: [
      "`--output-root <dir>` writes every card and the manifest under `<dir>`.",
      "Omitting the flag preserves today's default behavior.",
    ],
    touches: ["SKILL.md", "lib/cards/output.py"],
  },
  {
    id: "b044-01-planner-rate-limit",
    title: "Rate-limit the public planner endpoint",
    project: "C:\\dev\\agile-cards-board",
    status: "backlog",
    points: 3,
    stakes: "medium",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    batch: "b044",
    estimatedTokens: 21000,
    estimatedMinutes: 26,
    created: "2026-05-17",
    sizingNote: "medium stakes, shallow -- tier 3, sonnet without thinking",
    context:
      "`POST /api/stories/submit` spawns a planner subprocess. Without a " +
      "limit, a careless caller can fork enough planners to pin the host.",
    scope: [
      "Add a per-token token-bucket limiter in front of the submit route.",
      "Return 429 with a `Retry-After` header on overage.",
      "Make the limit configurable via an env var with a sane default.",
    ],
    outOfScope: [
      "Rate-limiting read routes. The cost is in spawning planners.",
    ],
    acceptance: [
      "A burst past the limit gets 429 with a valid `Retry-After`.",
      "Under-limit traffic is unaffected.",
    ],
    touches: ["backend/src/routes/stories.ts", "backend/src/config.ts"],
  },
  {
    id: "b044-02-sprint-planner-ui",
    title: "Build the sprint-planner timeline UI",
    project: "C:\\dev\\agile-cards-board",
    status: "backlog",
    points: 4,
    stakes: "medium",
    difficulty: "deep",
    model: "claude-sonnet-4-6",
    extendedThinking: true,
    modelFloor: "sonnet",
    batch: "b044",
    estimatedTokens: 47000,
    estimatedMinutes: 56,
    created: "2026-05-17",
    sizingNote: "medium stakes + deep (drag model + budget math) -- tier 4",
    context:
      "The backend already speaks `/api/sprints`, but the Sprint Planner page " +
      "is a placeholder. This card builds the real timeline: drag backlog " +
      "cards onto sprints and watch a points budget fill up.",
    scope: [
      "Render sprints as columns with start/end dates and a points budget.",
      "Let cards be dragged from the backlog onto a sprint.",
      "Show budget-used vs budget-total, summing each card's `points`.",
      "Persist sprint membership through `POST /api/sprints/:id/cards`.",
    ],
    outOfScope: [
      "Auto-suggesting a sprint plan. Manual placement for v1.",
      "Burndown charts -- a retros-side concern.",
    ],
    acceptance: [
      "A card dragged onto a sprint persists and survives a reload.",
      "The budget indicator updates as cards are added and removed.",
    ],
    touches: ["frontend/src/routes/SprintPlanner.tsx"],
  },
  {
    id: "b045-01-nexus-ingest-endpoint",
    title: "Add a batch-ingest endpoint to project-nexus",
    project: "C:\\dev\\project-nexus",
    status: "backlog",
    points: 3,
    stakes: "medium",
    difficulty: "shallow",
    model: "claude-sonnet-4-6",
    extendedThinking: false,
    modelFloor: "sonnet",
    batch: "b045",
    estimatedTokens: 24000,
    estimatedMinutes: 30,
    created: "2026-05-18",
    sizingNote: "medium stakes, shallow -- tier 3, sonnet without thinking",
    context:
      "project-nexus ingests records one at a time, which is slow for bulk " +
      "loads. A batch endpoint would let a caller submit many records in a " +
      "single request with all-or-nothing semantics.",
    scope: [
      "Add `POST /ingest/batch` accepting an array of records.",
      "Validate the whole batch before writing anything.",
      "Wrap the write in a transaction so a bad record rolls the batch back.",
    ],
    outOfScope: [
      "Streaming ingest. A bounded array is enough for the known callers.",
    ],
    acceptance: [
      "A valid batch is written atomically and returns per-record ids.",
      "One bad record rejects the whole batch with a clear error.",
    ],
    touches: ["src/api/ingest.py", "tests/api/test_batch_ingest.py"],
  },
  {
    id: "b043-02-postgres-substrate",
    title: "Add a Postgres store behind the storage substrate",
    project: "C:\\dev\\agile-cards",
    status: "blocked",
    points: 6,
    stakes: "high",
    difficulty: "deep",
    model: "claude-opus-4-6",
    extendedThinking: true,
    modelFloor: "opus",
    pinRequired: true,
    batch: "b043",
    estimatedTokens: 138000,
    estimatedMinutes: 180,
    created: "2026-05-16",
    dependsOn: ["b043-01-storage-substrate-v2"],
    mergeStatus: "blocked",
    sizingNote:
      "high stakes (new system of record) + deep -- tier 6, pinned, blocked on design",
    context:
      "Once the storage substrate is designed, a Postgres-backed `CardStore` " +
      "lets a fleet of runners share state without a shared filesystem. " +
      "Blocked until b043-01 lands the interface and the ADR.",
    scope: [
      "Implement a Postgres `CardStore` against the b043-01 interface.",
      "Add a schema migration and a connection-pool config.",
      "Port the runner suite to run against both stores in CI.",
    ],
    outOfScope: [
      "Live data migration tooling -- a separate card once this is proven.",
    ],
    acceptance: [
      "The runner suite passes against the Postgres store.",
      "Filesystem and Postgres stores are behaviorally identical under test.",
    ],
    touches: ["lib/store/postgres.py", "migrations/0001_cards.sql"],
    pointers: [
      "Blocked: needs the `CardStore` interface from b043-01-storage-substrate-v2.",
    ],
  },
  {
    id: "b044-03-multi-tenant-tokens",
    title: "Isolate bearer tokens per tenant",
    project: "C:\\dev\\agile-cards-board",
    status: "blocked",
    points: 5,
    stakes: "high",
    difficulty: "deep",
    model: "claude-opus-4-6",
    extendedThinking: true,
    modelFloor: "opus",
    batch: "b044",
    estimatedTokens: 88000,
    estimatedMinutes: 105,
    created: "2026-05-17",
    startedAt: "2026-05-18T20:00:00Z",
    finishedAt: "2026-05-19T09:30:00Z",
    claimedBy: "runner-02",
    modelUsed: "claude-opus-4-6",
    actualTokens: 96400,
    actualMinutes: 118,
    verifiedAt: "2026-05-19T09:40:00Z",
    verifiedBy: "cold-read-verifier (claude-sonnet-4-6)",
    mergeStatus: "conflict",
    sizingNote:
      "high stakes (auth tenancy) + deep -- tier 5; finished, blocked on a merge conflict",
    context:
      "The token store is single-tenant. This card scopes every token to a " +
      "tenant id so one dashboard install can serve isolated teams. The work " +
      "is finished and verified but the branch conflicts with the auth " +
      "changes from b040-03's follow-up; blocked until that is rebased.",
    scope: [
      "Add a `tenant_id` column to the `tokens` table and the mint flow.",
      "Scope `requireAuth` so a token only sees its tenant's cards.",
      "Backfill existing tokens into a default tenant.",
    ],
    outOfScope: [
      "A tenant-management UI. CLI provisioning is enough for now.",
    ],
    acceptance: [
      "A token from tenant A cannot read tenant B's cards.",
      "Existing tokens keep working under the default tenant.",
    ],
    touches: ["backend/src/auth/tokens.ts", "backend/src/routes/auth.ts"],
    pointers: [
      "Blocked: branch conflicts with the auth refactor; needs a rebase before merge.",
    ],
  },
];

main();
