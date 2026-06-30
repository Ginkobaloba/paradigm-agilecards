import { describe, expect, it } from "vitest";

import { buildApiPath, deriveRouterBasename } from "./baseUrl";

describe("deriveRouterBasename", () => {
  it("returns '/' at the root", () => {
    expect(deriveRouterBasename("/")).toBe("/");
  });

  it("returns '/' when undefined or empty", () => {
    expect(deriveRouterBasename(undefined)).toBe("/");
    expect(deriveRouterBasename("")).toBe("/");
  });

  it("strips a single trailing slash", () => {
    expect(deriveRouterBasename("/board/")).toBe("/board");
  });

  it("preserves a non-slashed base", () => {
    expect(deriveRouterBasename("/board")).toBe("/board");
  });

  it("handles nested base paths", () => {
    expect(deriveRouterBasename("/portal/board/")).toBe("/portal/board");
  });
});

describe("buildApiPath", () => {
  it("returns the path unchanged at the root", () => {
    expect(buildApiPath("/", "/api/cards")).toBe("/api/cards");
    expect(buildApiPath(undefined, "/api/cards")).toBe("/api/cards");
  });

  it("prefixes a base path", () => {
    expect(buildApiPath("/board/", "/api/cards")).toBe("/board/api/cards");
    expect(buildApiPath("/board/", "/events")).toBe("/board/events");
    expect(buildApiPath("/board/", "/healthz")).toBe("/board/healthz");
  });

  it("does not double the separator", () => {
    expect(buildApiPath("/board/", "/api/cards/id-1")).toBe(
      "/board/api/cards/id-1"
    );
  });

  it("rejects paths without a leading slash", () => {
    expect(() => buildApiPath("/board/", "api/cards")).toThrow(
      /must.*start.*\/|requires a path starting with/i
    );
  });

  it("preserves query strings", () => {
    expect(buildApiPath("/board/", "/events?token=abc")).toBe(
      "/board/events?token=abc"
    );
  });
});
