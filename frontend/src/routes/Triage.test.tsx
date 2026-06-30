/**
 * Triage route tests. The API layer is mocked; the card store is
 * seeded directly so the "Similar to" dedup runs against real
 * similarity math (not a stub).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { TriageBatch } from "../lib/api";
import type { RatesPayload } from "../lib/cost";
import { useStore } from "../state/store";
import { Triage } from "./Triage";

const listMock = vi.fn();
const promoteMock = vi.fn(async (_batchId: string, _file: string) => ({
  id: "b9-01",
  status: "backlog" as const,
  rank: 1024,
}));
const declineMock = vi.fn(async (_batchId: string, _file: string) => ({
  ok: true,
}));
const mergeMock = vi.fn(
  async (_batchId: string, _file: string, targetId: string) => ({
    ok: true,
    targetId,
  })
);

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    triageApi: {
      list: (): Promise<{ batches: TriageBatch[] }> =>
        listMock() as Promise<{ batches: TriageBatch[] }>,
      promote: (batchId: string, file: string) =>
        promoteMock(batchId, file),
      decline: (batchId: string, file: string) =>
        declineMock(batchId, file),
      merge: (batchId: string, file: string, targetId: string) =>
        mergeMock(batchId, file, targetId),
    },
  };
});

const RATES: RatesPayload = {
  rates: [
    {
      model: "claude-sonnet-4-6",
      inputPerMTokens: 3,
      outputPerMTokens: 15,
    },
  ],
  defaultInputRatio: 0.6,
};

const BATCH: TriageBatch = {
  batchId: "b900",
  story: "Resilience work",
  cards: [
    {
      id: "b900-01",
      title: "Add rate limiting middleware",
      file: "b900-01-rate-limit.md",
      bodyExcerpt: "Throttle inbound requests.",
      tier: 2,
      model: "claude-sonnet-4-6",
      estimatedTokens: 1_000_000,
      dependsOn: [],
    },
  ],
};

function seedExistingCard(): void {
  useStore.getState().setAll([
    {
      id: "ex-01",
      file: "/cards/backlog/ex-01.md",
      status: "backlog",
      frontmatter: { title: "Rate limiting middleware v2" },
      mtimeMs: 1,
    },
  ]);
}

function renderTriage() {
  render(<Triage rates={RATES} />);
}

describe("Triage route", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    useStore.setState({ cards: {}, ranks: {}, hydrated: false });
    listMock.mockResolvedValue({ batches: [BATCH] });
  });

  it("renders staged cards with excerpt, tier, and dollar estimate", async () => {
    renderTriage();
    expect(
      await screen.findByText("Add rate limiting middleware")
    ).toBeDefined();
    expect(screen.getByText("Throttle inbound requests.")).toBeDefined();
    expect(screen.getByText("tier 2")).toBeDefined();
    // 1M tokens at 0.6 input ratio: 0.6*3 + 0.4*15 = $7.80.
    expect(screen.getByText(/\$7\.80/)).toBeDefined();
  });

  it("shows the empty state when nothing is staged", async () => {
    listMock.mockResolvedValue({ batches: [] });
    renderTriage();
    expect(await screen.findByText(/nothing to triage/i)).toBeDefined();
  });

  it("flags similar existing cards and merges into one", async () => {
    seedExistingCard();
    renderTriage();
    expect(await screen.findByText("Similar to:")).toBeDefined();
    expect(screen.getByText("Rate limiting middleware v2")).toBeDefined();

    await userEvent.click(screen.getByRole("button", { name: /merge into/i }));
    await waitFor(() =>
      expect(mergeMock).toHaveBeenCalledWith(
        "b900",
        "b900-01-rate-limit.md",
        "ex-01"
      )
    );
    // The list refetches after the action.
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("shows no similar section when nothing matches", async () => {
    useStore.getState().setAll([
      {
        id: "far-01",
        file: "/cards/backlog/far-01.md",
        status: "backlog",
        frontmatter: { title: "Sprint retro notes UI" },
        mtimeMs: 1,
      },
    ]);
    renderTriage();
    await screen.findByText("Add rate limiting middleware");
    expect(screen.queryByText("Similar to:")).toBeNull();
  });

  it("promotes a card and refetches", async () => {
    renderTriage();
    await screen.findByText("Add rate limiting middleware");
    await userEvent.click(screen.getByRole("button", { name: "Promote" }));
    await waitFor(() =>
      expect(promoteMock).toHaveBeenCalledWith("b900", "b900-01-rate-limit.md")
    );
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("declines a card and refetches", async () => {
    renderTriage();
    await screen.findByText("Add rate limiting middleware");
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    await waitFor(() =>
      expect(declineMock).toHaveBeenCalledWith("b900", "b900-01-rate-limit.md")
    );
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("surfaces an API error", async () => {
    listMock.mockRejectedValue(new Error("boom"));
    renderTriage();
    expect(await screen.findByText(/boom/)).toBeDefined();
  });
});
