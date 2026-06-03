/**
 * Thin fetch wrapper. Every call sends the bearer token, surfaces a
 * useful error shape, and never returns `any`. If the backend changes
 * its response shape, narrow the types here once instead of every
 * caller.
 */

import { apiPath } from "./baseUrl";
import { getToken } from "./auth";

export type StatusId =
  | "backlog"
  | "active"
  | "awaiting_amendment_review"
  | "done"
  | "blocked";

export interface Column {
  id: StatusId;
  label: string;
}

export interface CardSummary {
  id: string;
  file: string;
  status: StatusId;
  frontmatter: Record<string, unknown>;
  mtimeMs: number;
}

export interface CardDetail extends CardSummary {
  body: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;
  constructor(status: number, message: string, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(apiPath(path), { ...init, headers });
  const text = await res.text();
  let payload: unknown = null;
  if (text.length > 0) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!res.ok) {
    const message =
      (typeof payload === "object" &&
        payload !== null &&
        "error" in payload &&
        typeof (payload as { error: unknown }).error === "string" &&
        (payload as { error: string }).error) ||
      `HTTP ${res.status}`;
    throw new ApiError(res.status, message, payload);
  }
  return payload as T;
}

export interface RankRow {
  cardId: string;
  status: StatusId;
  rank: number;
}

export interface CardEventRow {
  id: number;
  cardId: string;
  type: string;
  at: string;
  details: unknown;
}

export interface MoveResponse {
  id: string;
  file: string;
  status: StatusId;
  rank?: number;
}

export const api = {
  health: (): Promise<{ ok: boolean; cardsDir: string; version: string }> =>
    request("/healthz"),

  listColumns: (): Promise<{ columns: Column[] }> => request("/api/columns"),

  listCards: (): Promise<{ cards: CardSummary[] }> => request("/api/cards"),

  getCard: (id: string): Promise<CardDetail> =>
    request(`/api/cards/${encodeURIComponent(id)}`),

  moveCard: (id: string, status: StatusId): Promise<MoveResponse> =>
    request(`/api/cards/${encodeURIComponent(id)}/move`, {
      method: "POST",
      body: JSON.stringify({ status }),
    }),

  /**
   * Update a whitelisted set of scalar frontmatter fields on a card.
   * v1 accepts `stakes` and `cost_cap_usd`; the backend rejects
   * anything else with a 400. The grid's drag-to-restake handler is
   * the primary caller.
   */
  patchCardFrontmatter: (
    id: string,
    patch: { stakes?: string | null; cost_cap_usd?: number | null }
  ): Promise<CardSummary> =>
    request(`/api/cards/${encodeURIComponent(id)}/frontmatter`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  listRates: (): Promise<import("./cost").RatesPayload> => request("/api/rates"),

  listRanks: (): Promise<{ ranks: RankRow[] }> => request("/api/ranks"),

  listCardEvents: (
    id: string,
    opts: { limit?: number; since?: string } = {}
  ): Promise<{ events: CardEventRow[] }> => {
    const params = new URLSearchParams();
    if (typeof opts.limit === "number") params.set("limit", String(opts.limit));
    if (opts.since) params.set("since", opts.since);
    const q = params.toString();
    return request(
      `/api/cards/${encodeURIComponent(id)}/events${q ? `?${q}` : ""}`
    );
  },

  setRank: (
    id: string,
    status: StatusId,
    prevId: string | null,
    nextId: string | null
  ): Promise<{ cardId: string; status: StatusId; rank: number }> =>
    request(`/api/cards/${encodeURIComponent(id)}/rank`, {
      method: "POST",
      body: JSON.stringify({ status, prevId, nextId }),
    }),

  listViews: (): Promise<{ views: SavedView[] }> => request("/api/views"),

  createView: (name: string, payload: unknown): Promise<SavedView> =>
    request("/api/views", {
      method: "POST",
      body: JSON.stringify({ name, payload }),
    }),

  updateView: (
    id: number,
    patch: { name?: string; payload?: unknown }
  ): Promise<SavedView> =>
    request(`/api/views/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  deleteView: (id: number): Promise<void> =>
    request(`/api/views/${id}`, { method: "DELETE" }),
};

export interface SavedView {
  id: number;
  tokenId: number;
  name: string;
  payload: unknown;
  createdAt: string;
  updatedAt: string;
}

export type SprintStatus = "planning" | "active" | "completed" | "cancelled";

export interface Sprint {
  id: number;
  name: string;
  startsAt: string;
  endsAt: string;
  goal: string | null;
  status: SprintStatus;
  pointsTarget: number | null;
  dollarTarget: number | null;
  reviewHoursTarget: number | null;
  archivedAt: string | null;
  createdAt: string;
}

export interface SprintSummary extends Sprint {
  cardCount: number;
  plannedPointsSum: number;
}

export interface SprintCardLink {
  sprintId: number;
  cardId: string;
  plannedPoints: number | null;
}

export interface SprintCreate {
  name: string;
  startsAt: string;
  endsAt: string;
  goal?: string | null;
  status?: SprintStatus;
}

export interface SprintPatch {
  name?: string;
  startsAt?: string;
  endsAt?: string;
  goal?: string | null;
  status?: SprintStatus;
  pointsTarget?: number | null;
  dollarTarget?: number | null;
  reviewHoursTarget?: number | null;
  archivedAt?: string | null;
}

export const sprintsApi = {
  list: (
    opts: { includeArchived?: boolean } = {}
  ): Promise<{ sprints: SprintSummary[] }> => {
    const q = opts.includeArchived ? "?includeArchived=1" : "";
    return request(`/api/sprints${q}`);
  },
  create: (body: SprintCreate): Promise<{ sprint: Sprint }> =>
    request("/api/sprints", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  get: (
    id: number
  ): Promise<{ sprint: Sprint; cards: SprintCardLink[] }> =>
    request(`/api/sprints/${id}`),
  patch: (id: number, patch: SprintPatch): Promise<{ sprint: Sprint }> =>
    request(`/api/sprints/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  addCard: (
    sprintId: number,
    cardId: string,
    plannedPoints: number | null
  ): Promise<void> =>
    request(`/api/sprints/${sprintId}/cards`, {
      method: "POST",
      body: JSON.stringify({ cardId, plannedPoints }),
    }),
  removeCard: (sprintId: number, cardId: string): Promise<void> =>
    request(
      `/api/sprints/${sprintId}/cards/${encodeURIComponent(cardId)}`,
      { method: "DELETE" }
    ),
};
