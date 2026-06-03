/**
 * Tests for the dual-axis grid view.
 *
 * Two halves:
 *   1. Mounted-component tests for what the operator sees -- the plot,
 *      the plotted vs ungraphed split, the axis pickers (which exclude
 *      each other), the restake hint, the quadrant labels, and the
 *      click-to-open-card wiring.
 *   2. Pure tests for `restakeFromDrag`, the drag-to-restake decision the
 *      handler delegates to. We test the decision logic directly rather
 *      than simulating a dnd-kit pointer drag, which is flaky in jsdom
 *      (PointerSensor needs pointer-capture APIs jsdom doesn't implement)
 *      and would test the harness more than the gesture. The component
 *      handler is a thin shell over this helper, so covering the helper
 *      covers the gesture's real behavior.
 *
 * vitest runs with `globals: false`, so everything is imported explicitly
 * and we stick to plain DOM assertions (no jest-dom matchers are set up).
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";

import type { CardSummary } from "../lib/api";
import type { RatesPayload } from "../lib/cost";
import { restakeFromDrag } from "../lib/gridLayout";
import { useStore } from "../state/store";
import { useFilters } from "../state/filters";

// Stub the card modal so the mount tests exercise Grid's open-card wiring
// without pulling in Radix Dialog's portal + focus machinery (which leans
// on pointer-capture APIs jsdom lacks). The stub just echoes the card id
// it was handed, which is exactly the state Grid is responsible for.
vi.mock("../components/CardModal", () => ({
  CardModal: ({ cardId }: { cardId: string | null }) =>
    cardId ? <div data-testid="card-modal">{cardId}</div> : null,
}));

import { Grid } from "./Grid";

const RATES: RatesPayload = {
  rates: [
    {
      model: "claude-sonnet-4-6",
      displayName: "Sonnet 4.6",
      inputPerMTokens: 3,
      outputPerMTokens: 15,
    },
  ],
  defaultInputRatio: 0.6,
};

function makeCard(
  id: string,
  fm: Record<string, unknown>,
  status: CardSummary["status"] = "backlog"
): CardSummary {
  return { id, file: `/tmp/${id}.md`, status, frontmatter: fm, mtimeMs: 0 };
}

// A: cheap + low stakes, B: pricey + high stakes -> both plot. C has no
// token data, so it has no cost value and lands in the ungraphed tray
// under the default (x = cost) axis.
const CARD_A = makeCard("a1", {
  title: "Alpha card",
  model: "claude-sonnet-4-6",
  estimated_tokens: 1_000_000,
  stakes: "low",
});
const CARD_B = makeCard("b1", {
  title: "Bravo card",
  model: "claude-sonnet-4-6",
  estimated_tokens: 8_000_000,
  stakes: "high",
});
const CARD_C = makeCard("c1", {
  title: "Charlie card",
  stakes: "medium",
});

function seed(cards: CardSummary[]): void {
  useStore.getState().setAll(cards);
}

function renderGrid(): void {
  render(<Grid rates={RATES} />);
}

beforeEach(() => {
  cleanup();
  useFilters.getState().reset();
  useStore.setState({ cards: {}, ranks: {}, hydrated: false });
});

describe("Grid view (mounted)", () => {
  it("renders the plot surface", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    expect(screen.getByTestId("grid-plot")).toBeDefined();
  });

  it("plots the cards that have a value on both axes", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    expect(screen.getByText(/2 plotted/i)).toBeDefined();
  });

  it("drops a card missing an axis value into the ungraphed tray", () => {
    seed([CARD_A, CARD_B, CARD_C]);
    renderGrid();
    // A + B plot; C has no cost, so it is the one missing-data card.
    expect(screen.getByText(/2 plotted/i)).toBeDefined();
    expect(screen.getByText(/1 missing data/i)).toBeDefined();
    expect(screen.getByText(/Missing axis data \(1\)/i)).toBeDefined();
    // The tray surfaces the card so the operator can still open it.
    expect(screen.getByText("Charlie card")).toBeDefined();
  });

  it("excludes the current Y axis from the X axis picker", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    const xSelect = screen.getByLabelText(/x axis/i) as HTMLSelectElement;
    const values = Array.from(xSelect.options).map((o) => o.value);
    // Y defaults to stakes, so stakes must not be offered on X.
    expect(values).not.toContain("stakes");
    expect(values).toContain("cost");
  });

  it("excludes the current X axis from the Y axis picker", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    const ySelect = screen.getByLabelText(/y axis/i) as HTMLSelectElement;
    const values = Array.from(ySelect.options).map((o) => o.value);
    // X defaults to cost, so cost must not be offered on Y.
    expect(values).not.toContain("cost");
    expect(values).toContain("stakes");
  });

  it("shows the restake hint while Y is the editable stakes axis", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    expect(screen.getByText(/drag Y to restake/i)).toBeDefined();
  });

  it("shows the no-op hint once Y is switched off stakes", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    const ySelect = screen.getByLabelText(/y axis/i) as HTMLSelectElement;
    fireEvent.change(ySelect, { target: { value: "points" } });
    expect(screen.getByText(/drag = no-op \(v1\)/i)).toBeDefined();
  });

  it("renders the four quadrant labels", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    expect(screen.getByText("priority")).toBeDefined();
    expect(screen.getByText("do carefully")).toBeDefined();
    expect(screen.getByText("backlog")).toBeDefined();
    expect(screen.getByText("cancel / downtier")).toBeDefined();
  });

  it("opens the card when a plotted tile is clicked", () => {
    seed([CARD_A, CARD_B]);
    renderGrid();
    // No modal until a tile is clicked.
    expect(screen.queryByTestId("card-modal")).toBeNull();
    const tile = screen.getByRole("button", { name: /Alpha card/i });
    fireEvent.click(tile);
    const modal = screen.getByTestId("card-modal");
    expect(modal.textContent).toBe("a1");
  });
});

describe("restakeFromDrag (drag-to-restake decision)", () => {
  it("raises the bucket when the point is dragged upward", () => {
    // A low-stakes point (math-Y 0, screen bottom) dragged up half the
    // plot lands at math-Y 0.5 -> medium.
    const out = restakeFromDrag({
      yNorm: 0,
      deltaYPx: -100,
      plotHeightPx: 200,
      currentStakes: "low",
    });
    expect(out).toEqual({ targetStakes: "medium" });
  });

  it("lowers the bucket when the point is dragged downward", () => {
    // A high-stakes point (math-Y 1, screen top) dragged down half the
    // plot lands at math-Y 0.5 -> medium.
    const out = restakeFromDrag({
      yNorm: 1,
      deltaYPx: 100,
      plotHeightPx: 200,
      currentStakes: "high",
    });
    expect(out).toEqual({ targetStakes: "medium" });
  });

  it("returns null for a no-op drag and for a degenerate plot height", () => {
    // Tiny nudge that stays in the same bucket -> no write.
    expect(
      restakeFromDrag({
        yNorm: 1,
        deltaYPx: -10,
        plotHeightPx: 400,
        currentStakes: "high",
      })
    ).toBeNull();
    // Zero height -> can't translate the delta, so no write.
    expect(
      restakeFromDrag({
        yNorm: 0,
        deltaYPx: -100,
        plotHeightPx: 0,
        currentStakes: "low",
      })
    ).toBeNull();
  });
});
