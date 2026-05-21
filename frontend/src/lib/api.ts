/**
 * Thin fetch wrapper. Every call sends the bearer token, surfaces a
 * useful error shape, and never returns `any`. If the backend changes
 * its response shape, narrow the types here once instead of every
 * caller.
 */

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

  const res = await fetch(path, { ...init, headers });
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

  listRates: (): Promise<import("./cost").RatesPayload> => request("/api/rates"),

  listRanks: (): Promise<{ ranks: RankRow[] }> => request("/api/ranks"),

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
};
