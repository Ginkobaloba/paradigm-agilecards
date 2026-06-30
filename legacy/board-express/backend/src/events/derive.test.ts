import test from "node:test";
import assert from "node:assert/strict";

import { deriveEvents, type CardSnapshot } from "./derive.js";

function snap(over: Partial<CardSnapshot> = {}): CardSnapshot {
  const { frontmatter, ...rest } = over;
  return {
    id: "card-1",
    status: "backlog",
    mtimeMs: Date.parse("2026-05-22T00:00:00Z"),
    ...rest,
    frontmatter: { ...(frontmatter ?? {}) },
  };
}

test("first-seen card emits a single 'discovered' event", () => {
  const s = snap({ status: "active" });
  const events = deriveEvents(null, s);
  assert.equal(events.length, 1);
  assert.equal(events[0]?.type, "discovered");
  assert.equal(events[0]?.cardId, "card-1");
  assert.deepEqual(events[0]?.details, { status: "active" });
});

test("no changes => no events", () => {
  const prev = snap({ frontmatter: { claimed_by: "drew" } });
  const curr = snap({ frontmatter: { claimed_by: "drew" } });
  assert.deepEqual(deriveEvents(prev, curr), []);
});

test("status change emits status_changed", () => {
  const prev = snap({ status: "backlog" });
  const curr = snap({ status: "active" });
  const events = deriveEvents(prev, curr);
  assert.equal(events.length, 1);
  assert.equal(events[0]?.type, "status_changed");
  assert.deepEqual(events[0]?.details, { from: "backlog", to: "active" });
});

test("claimed_by becoming non-null emits 'started' with by + model", () => {
  const prev = snap({ frontmatter: { claimed_by: null } });
  const curr = snap({
    frontmatter: {
      claimed_by: "runner-1",
      started_at: "2026-05-22T10:00:00Z",
      model_used: "claude-sonnet-4-6",
      model: "claude-sonnet-4-6",
    },
  });
  const events = deriveEvents(prev, curr);
  const started = events.find((e) => e.type === "started");
  assert.ok(started, "expected a started event");
  assert.deepEqual(started.details, {
    by: "runner-1",
    model: "claude-sonnet-4-6",
  });
  assert.equal(started.at, "2026-05-22T10:00:00.000Z");
});

test("claimed_by becoming null after being set emits 'released'", () => {
  const prev = snap({
    frontmatter: { claimed_by: "runner-1", started_at: "2026-05-22T10:00:00Z" },
  });
  const curr = snap({ frontmatter: { claimed_by: null } });
  const events = deriveEvents(prev, curr);
  const released = events.find((e) => e.type === "released");
  assert.ok(released, "expected a released event");
});

test("last_heartbeat update emits a heartbeat event with that timestamp", () => {
  const prev = snap({
    frontmatter: {
      claimed_by: "runner-1",
      last_heartbeat: "2026-05-22T10:00:00Z",
    },
  });
  const curr = snap({
    frontmatter: {
      claimed_by: "runner-1",
      last_heartbeat: "2026-05-22T10:05:00Z",
    },
  });
  const events = deriveEvents(prev, curr);
  assert.equal(events.length, 1);
  assert.equal(events[0]?.type, "heartbeat");
  assert.equal(events[0]?.at, "2026-05-22T10:05:00.000Z");
  assert.deepEqual(events[0]?.details, { by: "runner-1" });
});

test("identical last_heartbeat values don't re-emit", () => {
  const fm = {
    claimed_by: "runner-1",
    last_heartbeat: "2026-05-22T10:00:00Z",
  };
  const prev = snap({ frontmatter: fm });
  const curr = snap({ frontmatter: fm });
  assert.deepEqual(deriveEvents(prev, curr), []);
});

test("finished_at newly set emits 'finished' with tokens + model", () => {
  const prev = snap({ frontmatter: { claimed_by: "runner-1" } });
  const curr = snap({
    frontmatter: {
      claimed_by: "runner-1",
      finished_at: "2026-05-22T10:15:00Z",
      actual_tokens: 14_200,
      model_used: "claude-opus-4-7",
    },
  });
  const events = deriveEvents(prev, curr);
  const finished = events.find((e) => e.type === "finished");
  assert.ok(finished, "expected a finished event");
  assert.equal(finished.at, "2026-05-22T10:15:00.000Z");
  assert.deepEqual(finished.details, {
    tokens: 14_200,
    model: "claude-opus-4-7",
  });
});

test("verified_at newly set emits 'verifier_called' with the verifier", () => {
  const prev = snap({ frontmatter: { finished_at: "2026-05-22T10:15:00Z" } });
  const curr = snap({
    frontmatter: {
      finished_at: "2026-05-22T10:15:00Z",
      verified_at: "2026-05-22T10:17:00Z",
      verified_by: "verifier-claude-haiku",
    },
  });
  const events = deriveEvents(prev, curr);
  const v = events.find((e) => e.type === "verifier_called");
  assert.ok(v, "expected a verifier_called event");
  assert.equal(v.at, "2026-05-22T10:17:00.000Z");
  assert.deepEqual(v.details, { by: "verifier-claude-haiku" });
});

test("cascade_history growth emits one 'cascade' event per new entry", () => {
  const prev = snap({
    frontmatter: { cascade_history: [{ at: "2026-05-22T10:00:00Z", reason: "first" }] },
  });
  const curr = snap({
    frontmatter: {
      cascade_history: [
        { at: "2026-05-22T10:00:00Z", reason: "first" },
        { at: "2026-05-22T10:05:00Z", reason: "second" },
        { at: "2026-05-22T10:10:00Z", reason: "third" },
      ],
    },
  });
  const events = deriveEvents(prev, curr).filter((e) => e.type === "cascade");
  assert.equal(events.length, 2);
  assert.deepEqual(events[0]?.details, {
    at: "2026-05-22T10:05:00Z",
    reason: "second",
  });
  assert.deepEqual(events[1]?.details, {
    at: "2026-05-22T10:10:00Z",
    reason: "third",
  });
});

test("merge_status change emits merge_status_changed", () => {
  const prev = snap({ frontmatter: { merge_status: "pending" } });
  const curr = snap({ frontmatter: { merge_status: "merged" } });
  const events = deriveEvents(prev, curr);
  const m = events.find((e) => e.type === "merge_status_changed");
  assert.ok(m);
  assert.deepEqual(m.details, { from: "pending", to: "merged" });
});

test("multiple simultaneous frontmatter changes emit multiple events", () => {
  const prev = snap({ status: "backlog", frontmatter: { claimed_by: null } });
  const curr = snap({
    status: "active",
    frontmatter: {
      claimed_by: "runner-1",
      started_at: "2026-05-22T10:00:00Z",
      last_heartbeat: "2026-05-22T10:00:00Z",
      model_used: "claude-sonnet-4-6",
    },
  });
  const events = deriveEvents(prev, curr);
  const types = events.map((e) => e.type).sort();
  // status_changed + started + heartbeat -- claimed_by-set-with-heartbeat case
  assert.ok(types.includes("status_changed"));
  assert.ok(types.includes("started"));
  assert.ok(types.includes("heartbeat"));
});

test("derived events default to mtimeMs ISO when frontmatter has no timestamp", () => {
  const t = Date.parse("2026-05-22T12:34:56Z");
  const prev = snap({ status: "backlog", mtimeMs: t });
  const curr = snap({ status: "active", mtimeMs: t });
  const events = deriveEvents(prev, curr);
  assert.equal(events[0]?.at, "2026-05-22T12:34:56.000Z");
});

test("invalid timestamp strings fall back to mtimeMs", () => {
  const t = Date.parse("2026-05-22T12:34:56Z");
  const prev = snap({ frontmatter: { claimed_by: null }, mtimeMs: t });
  const curr = snap({
    frontmatter: {
      claimed_by: "runner-1",
      started_at: "not a date",
      model_used: "claude-sonnet-4-6",
    },
    mtimeMs: t,
  });
  const events = deriveEvents(prev, curr);
  const started = events.find((e) => e.type === "started");
  assert.equal(started?.at, "2026-05-22T12:34:56.000Z");
});
