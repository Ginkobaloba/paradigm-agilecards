/**
 * Sprint routes.
 *
 *   GET    /api/sprints                    -> list (with per-sprint rollups)
 *   POST   /api/sprints                    -> create
 *   GET    /api/sprints/:id                -> detail (with assigned cards)
 *   PATCH  /api/sprints/:id                -> edit any subset of fields
 *   POST   /api/sprints/:id/cards          -> assign / re-assign a card
 *   DELETE /api/sprints/:id/cards/:cardId  -> remove a card from a sprint
 *
 * Wire format is camelCase end-to-end. The SQL columns are snake_case,
 * but we shape on the way out and validate on the way in so the
 * frontend never sees the database's spellings.
 */

import { Router, type Request, type Response } from "express";

import { getDb } from "../db/sqlite.js";

export const SPRINT_STATUSES = [
  "planning",
  "active",
  "completed",
  "cancelled",
] as const;
export type SprintStatus = (typeof SPRINT_STATUSES)[number];

const VALID_STATUSES = new Set<string>(SPRINT_STATUSES);

interface SprintRow {
  id: number;
  name: string;
  starts_at: string;
  ends_at: string;
  goal: string | null;
  status: string;
  points_target: number | null;
  dollar_target: number | null;
  review_hours_target: number | null;
  archived_at: string | null;
  created_at: string;
}

interface SprintCardRow {
  sprint_id: number;
  card_id: string;
  planned_points: number | null;
}

interface SprintRollupRow {
  sprint_id: number;
  card_count: number;
  points_sum: number | null;
}

export interface SprintShape {
  id: number;
  name: string;
  startsAt: string;
  endsAt: string;
  goal: string | null;
  status: string;
  pointsTarget: number | null;
  dollarTarget: number | null;
  reviewHoursTarget: number | null;
  archivedAt: string | null;
  createdAt: string;
}

export interface SprintSummary extends SprintShape {
  cardCount: number;
  plannedPointsSum: number;
}

function shapeSprint(row: SprintRow): SprintShape {
  return {
    id: row.id,
    name: row.name,
    startsAt: row.starts_at,
    endsAt: row.ends_at,
    goal: row.goal,
    status: row.status,
    pointsTarget: row.points_target,
    dollarTarget: row.dollar_target,
    reviewHoursTarget: row.review_hours_target,
    archivedAt: row.archived_at,
    createdAt: row.created_at,
  };
}

const SELECT_COLUMNS =
  "id, name, starts_at, ends_at, goal, status, points_target, dollar_target, review_hours_target, archived_at, created_at";

export function sprintsRouter(): Router {
  const router = Router();

  router.get("/sprints", (req: Request, res: Response) => {
    const includeArchived =
      typeof req.query["includeArchived"] === "string" &&
      ["1", "true", "yes"].includes(
        String(req.query["includeArchived"]).toLowerCase()
      );

    const sprints = (
      getDb()
        .prepare(
          `SELECT ${SELECT_COLUMNS} FROM sprints ${
            includeArchived ? "" : "WHERE archived_at IS NULL"
          } ORDER BY starts_at DESC`
        )
        .all() as SprintRow[]
    ).map(shapeSprint);

    if (sprints.length === 0) {
      res.json({ sprints: [] });
      return;
    }

    const rollups = getDb()
      .prepare(
        `SELECT sprint_id, COUNT(*) AS card_count, COALESCE(SUM(planned_points), 0) AS points_sum
           FROM sprint_cards
          GROUP BY sprint_id`
      )
      .all() as SprintRollupRow[];

    const rollupById = new Map<number, SprintRollupRow>();
    for (const r of rollups) rollupById.set(r.sprint_id, r);

    const summaries: SprintSummary[] = sprints.map((s) => {
      const r = rollupById.get(s.id);
      return {
        ...s,
        cardCount: r?.card_count ?? 0,
        plannedPointsSum: r?.points_sum ?? 0,
      };
    });

    res.json({ sprints: summaries });
  });

  router.post("/sprints", (req: Request, res: Response) => {
    const body = req.body as
      | {
          name?: unknown;
          startsAt?: unknown;
          endsAt?: unknown;
          goal?: unknown;
          status?: unknown;
        }
      | undefined;
    const name = typeof body?.name === "string" ? body.name.trim() : "";
    const startsAt = typeof body?.startsAt === "string" ? body.startsAt : "";
    const endsAt = typeof body?.endsAt === "string" ? body.endsAt : "";
    const goal =
      typeof body?.goal === "string" && body.goal.length > 0
        ? body.goal
        : null;
    const status =
      typeof body?.status === "string" && VALID_STATUSES.has(body.status)
        ? body.status
        : "planning";

    if (!name) {
      res.status(400).json({ error: "name is required" });
      return;
    }
    if (!startsAt || !endsAt) {
      res
        .status(400)
        .json({ error: "startsAt and endsAt are required (ISO date)" });
      return;
    }
    if (endsAt < startsAt) {
      res.status(400).json({ error: "endsAt cannot be before startsAt" });
      return;
    }

    const info = getDb()
      .prepare<[string, string, string, string | null, string]>(
        `INSERT INTO sprints (name, starts_at, ends_at, goal, status)
         VALUES (?, ?, ?, ?, ?)`
      )
      .run(name, startsAt, endsAt, goal, status);
    const id = Number(info.lastInsertRowid);

    const row = getDb()
      .prepare<[number]>(`SELECT ${SELECT_COLUMNS} FROM sprints WHERE id = ?`)
      .get(id) as SprintRow;

    res.status(201).json({ sprint: shapeSprint(row) });
  });

  router.get("/sprints/:id", (req: Request, res: Response) => {
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "bad id" });
      return;
    }
    const row = getDb()
      .prepare<[number]>(`SELECT ${SELECT_COLUMNS} FROM sprints WHERE id = ?`)
      .get(id) as SprintRow | undefined;
    if (!row) {
      res.status(404).json({ error: "no such sprint" });
      return;
    }
    const cards = (
      getDb()
        .prepare<[number]>(
          `SELECT sprint_id, card_id, planned_points FROM sprint_cards WHERE sprint_id = ?`
        )
        .all(id) as SprintCardRow[]
    ).map((c) => ({
      sprintId: c.sprint_id,
      cardId: c.card_id,
      plannedPoints: c.planned_points,
    }));
    res.json({ sprint: shapeSprint(row), cards });
  });

  router.patch("/sprints/:id", (req: Request, res: Response) => {
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "bad id" });
      return;
    }
    const existing = getDb()
      .prepare<[number]>(`SELECT ${SELECT_COLUMNS} FROM sprints WHERE id = ?`)
      .get(id) as SprintRow | undefined;
    if (!existing) {
      res.status(404).json({ error: "no such sprint" });
      return;
    }

    const body = (req.body ?? {}) as {
      name?: unknown;
      startsAt?: unknown;
      endsAt?: unknown;
      goal?: unknown;
      status?: unknown;
      pointsTarget?: unknown;
      dollarTarget?: unknown;
      reviewHoursTarget?: unknown;
      archivedAt?: unknown;
    };

    // Build the SET clause from whichever fields the caller sent. Each
    // accepted field undergoes one of three validations:
    //   - typeof check
    //   - enum membership (status)
    //   - finite-number coercion (targets)
    const sets: string[] = [];
    const params: Array<string | number | null> = [];

    if (typeof body.name === "string") {
      const v = body.name.trim();
      if (!v) {
        res.status(400).json({ error: "name cannot be empty" });
        return;
      }
      sets.push("name = ?");
      params.push(v);
    }
    if (typeof body.startsAt === "string") {
      sets.push("starts_at = ?");
      params.push(body.startsAt);
    }
    if (typeof body.endsAt === "string") {
      sets.push("ends_at = ?");
      params.push(body.endsAt);
    }
    if (body.goal === null || typeof body.goal === "string") {
      sets.push("goal = ?");
      params.push(body.goal === null ? null : (body.goal as string));
    }
    if (typeof body.status === "string") {
      if (!VALID_STATUSES.has(body.status)) {
        res.status(400).json({
          error: "status must be one of",
          valid: Array.from(VALID_STATUSES),
        });
        return;
      }
      sets.push("status = ?");
      params.push(body.status);
    }
    if (body.pointsTarget === null || typeof body.pointsTarget === "number") {
      sets.push("points_target = ?");
      params.push(
        body.pointsTarget === null ? null : Math.max(0, Math.floor(body.pointsTarget))
      );
    }
    if (body.dollarTarget === null || typeof body.dollarTarget === "number") {
      sets.push("dollar_target = ?");
      params.push(
        body.dollarTarget === null ? null : Math.max(0, body.dollarTarget)
      );
    }
    if (
      body.reviewHoursTarget === null ||
      typeof body.reviewHoursTarget === "number"
    ) {
      sets.push("review_hours_target = ?");
      params.push(
        body.reviewHoursTarget === null
          ? null
          : Math.max(0, body.reviewHoursTarget)
      );
    }
    if (body.archivedAt === null || typeof body.archivedAt === "string") {
      sets.push("archived_at = ?");
      params.push(body.archivedAt === null ? null : (body.archivedAt as string));
    }

    if (sets.length === 0) {
      res.status(400).json({ error: "no recognized fields in body" });
      return;
    }

    params.push(id);
    getDb()
      .prepare(`UPDATE sprints SET ${sets.join(", ")} WHERE id = ?`)
      .run(...params);

    const row = getDb()
      .prepare<[number]>(`SELECT ${SELECT_COLUMNS} FROM sprints WHERE id = ?`)
      .get(id) as SprintRow;
    res.json({ sprint: shapeSprint(row) });
  });

  router.post("/sprints/:id/cards", (req: Request, res: Response) => {
    const id = Number.parseInt(req.params["id"] ?? "", 10);
    if (!Number.isFinite(id)) {
      res.status(400).json({ error: "bad sprint id" });
      return;
    }
    const body = req.body as
      | { cardId?: unknown; plannedPoints?: unknown }
      | undefined;
    const cardId = typeof body?.cardId === "string" ? body.cardId : "";
    const plannedPoints =
      typeof body?.plannedPoints === "number"
        ? Math.max(0, Math.floor(body.plannedPoints))
        : null;
    if (!cardId) {
      res.status(400).json({ error: "cardId required" });
      return;
    }

    const sprint = getDb()
      .prepare<[number]>(`SELECT id FROM sprints WHERE id = ?`)
      .get(id);
    if (!sprint) {
      res.status(404).json({ error: "no such sprint" });
      return;
    }

    getDb()
      .prepare<[number, string, number | null]>(
        `INSERT OR REPLACE INTO sprint_cards (sprint_id, card_id, planned_points)
         VALUES (?, ?, ?)`
      )
      .run(id, cardId, plannedPoints);

    res.status(204).end();
  });

  router.delete(
    "/sprints/:id/cards/:cardId",
    (req: Request, res: Response) => {
      const id = Number.parseInt(req.params["id"] ?? "", 10);
      const cardId = req.params["cardId"];
      if (!Number.isFinite(id) || !cardId) {
        res.status(400).json({ error: "bad id or cardId" });
        return;
      }
      getDb()
        .prepare<[number, string]>(
          `DELETE FROM sprint_cards WHERE sprint_id = ? AND card_id = ?`
        )
        .run(id, cardId);
      res.status(204).end();
    }
  );

  return router;
}
