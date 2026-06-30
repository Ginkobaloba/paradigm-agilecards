/**
 * Smoke test for the SubmitStory route.
 *
 * Scope (per the brief): the route mounts, the textarea accepts input,
 * the plan button enables once input is non-empty. We mock the SSE
 * client so no network calls happen.
 *
 * The real /cards invocation is exercised by the backend test, where a
 * fake invoker writes a known manifest. Here we only verify the
 * frontend wiring.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

// Mock the submit client so we don't try to fetch from jsdom.
vi.mock("../lib/submitStory", () => {
  return {
    streamSubmit: vi.fn(async function* () {
      // yields nothing by default
    }),
    approveBatch: vi.fn(async () => ({ batchId: "b1", cardsWritten: 0 })),
    cancelBatch: vi.fn(async () => {}),
    SubmitError: class SubmitError extends Error {
      stage: string | null;
      constructor(m: string, s: string | null) {
        super(m);
        this.stage = s;
      }
    },
  };
});

import { SubmitStory } from "./SubmitStory";
import { useSubmitStore } from "../state/submitStore";

function renderRoute(): void {
  render(
    <MemoryRouter initialEntries={["/submit"]}>
      <SubmitStory />
    </MemoryRouter>
  );
}

describe("SubmitStory route", () => {
  beforeEach(() => {
    useSubmitStore.getState().reset();
    cleanup();
  });

  it("renders the page heading", () => {
    renderRoute();
    expect(screen.getByText(/submit a story/i)).toBeDefined();
  });

  it("renders the story textarea and the plan button", () => {
    renderRoute();
    const ta = screen.getByLabelText(/^story$/i);
    expect(ta).toBeDefined();
    expect(ta.tagName).toBe("TEXTAREA");

    const btn = screen.getByRole("button", { name: /plan this story/i });
    expect(btn).toBeDefined();
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables the plan button once the textarea has content", async () => {
    renderRoute();
    const user = userEvent.setup();
    const ta = screen.getByLabelText(/^story$/i);
    await user.type(ta, "As an operator I want rate limits.");
    const btn = screen.getByRole("button", { name: /plan this story/i });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });

  it("exposes project picker with a 'no project' default", () => {
    renderRoute();
    const select = screen.getByLabelText(/^project$/i) as HTMLSelectElement;
    expect(select).toBeDefined();
    expect(select.value).toBe("");
    expect(select.options.length).toBeGreaterThan(1);
  });
});
