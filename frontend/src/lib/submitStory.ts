/**
 * Submit-story client.
 *
 * EventSource only does GET, but the submit endpoint is a POST so we
 * can carry the story body. So we use fetch() with a streaming body
 * reader and parse the SSE wire format manually. This is a tiny
 * adaptation, well documented in MDN.
 *
 * The reader yields one event at a time. Each event has a name
 * (`event: progress` etc.) and a JSON-encoded data line.
 */

import { getToken } from "./auth";
import { apiPath } from "./baseUrl";

export interface SubmitRequest {
  readonly story: string;
  readonly projectPath: string | null;
  readonly mode: "full" | "lean" | null;
  readonly deepPlanning: boolean;
}

export interface SseEvent {
  readonly event: string;
  readonly data: unknown;
}

export class SubmitError extends Error {
  readonly stage: string | null;
  constructor(message: string, stage: string | null) {
    super(message);
    this.name = "SubmitError";
    this.stage = stage;
  }
}

/**
 * Open a POST stream to /api/stories/submit. Yields parsed SSE events
 * as they arrive. Aborts cleanly if the AbortSignal trips.
 */
export async function* streamSubmit(
  req: SubmitRequest,
  signal: AbortSignal
): AsyncGenerator<SseEvent, void, void> {
  const token = getToken();
  const headers = new Headers({
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  });
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const body: Record<string, unknown> = { story: req.story };
  if (req.projectPath) body["project_path"] = req.projectPath;
  if (req.mode) body["mode"] = req.mode;
  if (req.deepPlanning) body["deep_planning"] = true;

  const res = await fetch(apiPath("/api/stories/submit"), {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = (await res.json()) as { error?: string };
      if (j && typeof j.error === "string") detail = j.error;
    } catch {
      /* swallow */
    }
    throw new SubmitError(detail, "submit");
  }

  if (!res.body) {
    throw new SubmitError("no response body", "submit");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let idx = buf.indexOf("\n\n");
    while (idx !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const parsed = parseBlock(block);
      if (parsed) yield parsed;
      idx = buf.indexOf("\n\n");
    }
  }
}

function parseBlock(block: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  let data: unknown = raw;
  try {
    data = JSON.parse(raw);
  } catch {
    /* keep raw */
  }
  return { event, data };
}

export interface ApprovedResult {
  readonly batchId: string;
  readonly cardsWritten: number;
}

export async function approveBatch(batchId: string): Promise<ApprovedResult> {
  const token = getToken();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(
    apiPath(`/api/stories/${encodeURIComponent(batchId)}/approve`),
    { method: "POST", headers }
  );
  const j = (await res.json()) as
    | { batchId: string; cardsWritten: number }
    | { error: string };
  if (!res.ok) {
    const err = "error" in j ? j.error : `HTTP ${res.status}`;
    throw new SubmitError(err, "approve");
  }
  if (!("cardsWritten" in j)) {
    throw new SubmitError("malformed approve response", "approve");
  }
  return { batchId: j.batchId, cardsWritten: j.cardsWritten };
}

export async function cancelBatch(batchId: string): Promise<void> {
  const token = getToken();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (token) headers.set("Authorization", `Bearer ${token}`);
  await fetch(apiPath(`/api/stories/${encodeURIComponent(batchId)}/cancel`), {
    method: "POST",
    headers,
  }).catch(() => {
    /* best-effort */
  });
}
