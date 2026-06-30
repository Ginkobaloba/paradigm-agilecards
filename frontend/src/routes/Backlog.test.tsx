/**
 * Backlog grooming surface tests. The API layer is mocked; the card
 * store is seeded directly. Covers the table render, the ready/ice-box
 * filter, and the three inline-edit paths (ready toggle, title rename,
 * points re-tier) each writing back through patchCardFrontmatter.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { CardSummary } from "../lib/api";
import { useStore } from "../state/store";
import { Backlog } from "./Backlog";

const patchMock = vi.fn();
const getCardMock = vi.fn();

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return {
    ...original,
    api: {
      ...original.api,
      patchCardFrontmatter: (id: string, patch: unknown) =>
        patchMock(id, patch),
      getCard: (id: string) => getCardMock(id),
    },
  };
});

function card(
  id: string,
  fm: Record<string, unknown>,
  status: CardSummary["status"] = "backlog"
): CardSummary {
  return { id, file: `/cards/${status}/${id}.md`, status, frontmatter: fm, mtimeMs: 1 };
}

function seed(cards: CardSummary[]): void {
  useStore.getState().setAll(cards);
}

describe("Backlog grooming route", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    useStore.setState({ cards: {}, ranks: {}, hydrated: false });
    // Default: patch echoes the new frontmatter back.
    patchMock.mockImplementation(async (id: string, patch: Record<string, unknown>) => {
      const existing = useStore.getState().cards[id]!;
      return { ...existing, frontmatter: { ...existing.frontmatter, ...patch } };
    });
  });

  it("renders backlog cards in a dense table", () => {
    seed([
      card("b1-01", { title: "First card", points: 2, stakes: "low" }),
      card("b1-02", { title: "Second card", points: 4, stakes: "high" }),
    ]);
    render(<Backlog />);
    expect(screen.getByDisplayValue("First card")).toBeDefined();
    expect(screen.getByDisplayValue("Second card")).toBeDefined();
    expect(screen.getByText("b1-01")).toBeDefined();
  });

  it("excludes non-backlog cards", () => {
    seed([
      card("b1-01", { title: "Backlog card" }),
      card("a1-01", { title: "Active card" }, "active"),
    ]);
    render(<Backlog />);
    expect(screen.getByDisplayValue("Backlog card")).toBeDefined();
    expect(screen.queryByDisplayValue("Active card")).toBeNull();
  });

  it("filters to ready / ice-box", async () => {
    seed([
      card("b1-01", { title: "Ready one", ready: true }),
      card("b1-02", { title: "Icebox one" }),
    ]);
    render(<Backlog />);
    // All shown by default.
    expect(screen.getByDisplayValue("Ready one")).toBeDefined();
    expect(screen.getByDisplayValue("Icebox one")).toBeDefined();

    await userEvent.click(screen.getByRole("button", { name: "Ready" }));
    expect(screen.getByDisplayValue("Ready one")).toBeDefined();
    expect(screen.queryByDisplayValue("Icebox one")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Ice-box" }));
    expect(screen.getByDisplayValue("Icebox one")).toBeDefined();
    expect(screen.queryByDisplayValue("Ready one")).toBeNull();
  });

  it("toggles ready and writes back", async () => {
    seed([card("b1-01", { title: "Toggle me" })]);
    render(<Backlog />);
    const toggle = screen.getByRole("switch");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    await userEvent.click(toggle);
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("b1-01", { ready: true })
    );
    expect(useStore.getState().cards["b1-01"]!.frontmatter["ready"]).toBe(true);
  });

  it("renames via the title field on blur", async () => {
    seed([card("b1-01", { title: "Old name" })]);
    render(<Backlog />);
    const input = screen.getByDisplayValue("Old name");
    await userEvent.clear(input);
    await userEvent.type(input, "New name");
    await userEvent.tab();
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("b1-01", { title: "New name" })
    );
  });

  it("does not write an unchanged title on blur", async () => {
    seed([card("b1-01", { title: "Same name" })]);
    render(<Backlog />);
    const input = screen.getByDisplayValue("Same name");
    await userEvent.click(input);
    await userEvent.tab();
    expect(patchMock).not.toHaveBeenCalled();
  });

  it("re-tiers via the points select", async () => {
    seed([card("b1-01", { title: "Tier me", points: 2 })]);
    render(<Backlog />);
    const select = screen.getByLabelText("points for Tier me");
    await userEvent.selectOptions(select, "5");
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("b1-01", { points: 5 })
    );
  });

  it("rolls back and surfaces an error when the patch fails", async () => {
    seed([card("b1-01", { title: "Doomed" })]);
    patchMock.mockRejectedValue(new Error("disk full"));
    getCardMock.mockResolvedValue(
      card("b1-01", { title: "Doomed" })
    );
    render(<Backlog />);
    await userEvent.click(screen.getByRole("switch"));
    expect(await screen.findByText(/disk full/)).toBeDefined();
    // Rolled back to not-ready via the server re-fetch.
    expect(useStore.getState().cards["b1-01"]!.frontmatter["ready"]).not.toBe(
      true
    );
  });

  it("shows an empty state with no backlog cards", () => {
    seed([card("a1-01", { title: "Active" }, "active")]);
    render(<Backlog />);
    expect(screen.getByText(/No cards in the backlog/i)).toBeDefined();
  });

  it("counts ready cards in the header", () => {
    seed([
      card("b1-01", { title: "r1", ready: true }),
      card("b1-02", { title: "r2", ready: true }),
      card("b1-03", { title: "i1" }),
    ]);
    render(<Backlog />);
    expect(screen.getByText("2/3 ready")).toBeDefined();
  });
});
