/**
 * Round-trip tests for the saved-view bundle: saving captures filters +
 * group-by lens + grid axes, loading applies all three, and a legacy
 * (filters-only) row loads without dragging stale state along.
 *
 * The backend is mocked; these test the ViewMenu wiring against the
 * stores, not the network. vitest runs `globals: false`, so imports are
 * explicit and assertions stick to plain DOM / store reads.
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  render,
  screen,
  cleanup,
  fireEvent,
  waitFor,
} from "@testing-library/react";

vi.mock("../lib/api", async (importActual) => {
  const actual = await importActual<typeof import("../lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listViews: vi.fn(async () => ({ views: [] })),
      createView: vi.fn(async (name: string, payload: unknown) => ({
        id: 1,
        tokenId: 1,
        name,
        payload,
        createdAt: "",
        updatedAt: "",
      })),
      updateView: vi.fn(),
      deleteView: vi.fn(async () => {}),
    },
  };
});

import { api, type SavedView } from "../lib/api";
import { ViewMenu } from "./ViewMenu";
import { EMPTY_FILTERS, useFilters } from "../state/filters";
import { useLens } from "../state/lens";
import {
  DEFAULT_X_AXIS,
  DEFAULT_Y_AXIS,
  useGridAxes,
} from "../state/gridAxes";

function view(over: Partial<SavedView> & { payload: unknown }): SavedView {
  return {
    id: 7,
    tokenId: 1,
    name: "Saved",
    createdAt: "",
    updatedAt: "",
    ...over,
  };
}

beforeEach(() => {
  cleanup();
  vi.clearAllMocks();
  useFilters.getState().reset();
  useLens.setState({ groupBy: "none" });
  useGridAxes.setState({ xAxis: DEFAULT_X_AXIS, yAxis: DEFAULT_Y_AXIS });
});

function openMenu(): void {
  fireEvent.click(screen.getByTitle("saved views"));
}

describe("ViewMenu save", () => {
  it("captures filters, group-by, and grid axes in the saved bundle", async () => {
    useFilters.getState().setAll({ ...EMPTY_FILTERS, project: ["acme"] });
    useLens.setState({ groupBy: "project" });
    useGridAxes.setState({ xAxis: "points", yAxis: "tier" });

    render(<ViewMenu />);
    openMenu();
    fireEvent.change(screen.getByPlaceholderText("Save as..."), {
      target: { value: "My view" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.createView).toHaveBeenCalledTimes(1));
    const [name, payload] = vi.mocked(api.createView).mock.calls[0]!;
    expect(name).toBe("My view");
    const bundle = payload as {
      views_schema_version: number;
      filters: { project: string[] };
      groupBy: string;
      grid: { xAxis: string; yAxis: string };
    };
    expect(bundle.views_schema_version).toBe(1);
    expect(bundle.filters.project).toEqual(["acme"]);
    expect(bundle.groupBy).toBe("project");
    expect(bundle.grid).toEqual({ xAxis: "points", yAxis: "tier" });
  });
});

describe("ViewMenu load", () => {
  it("applies a versioned bundle to filters, lens, and grid axes", async () => {
    vi.mocked(api.listViews).mockResolvedValueOnce({
      views: [
        view({
          id: 7,
          name: "Saved",
          payload: {
            views_schema_version: 1,
            filters: { ...EMPTY_FILTERS, project: ["zeta"] },
            groupBy: "project",
            grid: { xAxis: "points", yAxis: "tier" },
          },
        }),
      ],
    });

    render(<ViewMenu />);
    openMenu();
    fireEvent.click(await screen.findByText("Saved"));

    await waitFor(() =>
      expect(useFilters.getState().project).toEqual(["zeta"])
    );
    expect(useLens.getState().groupBy).toBe("project");
    expect(useGridAxes.getState().xAxis).toBe("points");
    expect(useGridAxes.getState().yAxis).toBe("tier");
  });

  it("loads a legacy filters-only row without carrying stale lens/axes", async () => {
    // Pre-set non-default lens + axes; loading a legacy row must reset
    // them to defaults (the legacy payload has no groupBy / grid).
    useLens.setState({ groupBy: "project" });
    useGridAxes.setState({ xAxis: "points", yAxis: "tier" });

    vi.mocked(api.listViews).mockResolvedValueOnce({
      views: [
        view({
          id: 9,
          name: "Old",
          // A bare FilterState, exactly how pre-versioning rows look.
          payload: { ...EMPTY_FILTERS, batch: ["b042"] },
        }),
      ],
    });

    render(<ViewMenu />);
    openMenu();
    fireEvent.click(await screen.findByText("Old"));

    await waitFor(() => expect(useFilters.getState().batch).toEqual(["b042"]));
    expect(useLens.getState().groupBy).toBe("none");
    expect(useGridAxes.getState().xAxis).toBe(DEFAULT_X_AXIS);
    expect(useGridAxes.getState().yAxis).toBe(DEFAULT_Y_AXIS);
  });
});
