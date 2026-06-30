import { useMemo } from "react";

import { useStore } from "../state/store";
import {
  activeFilterCount,
  chipOptions,
  useFilters,
} from "../state/filters";
import { useLens } from "../state/lens";
import { FilterChip, TriChip } from "./FilterChip";

interface FilterBarProps {
  onOpenDepView?: () => void;
}

/**
 * Sits above the kanban grid. Renders a search input plus a row of
 * dismissable filter chips. Each chip's options derive from the live
 * cards in the store -- a chip never shows a value that doesn't exist
 * on any visible card.
 *
 * Filter state lives in `useFilters`; the kanban's selector consumes
 * the same state to decide which cards to render.
 */
export function FilterBar({ onOpenDepView }: FilterBarProps = {}) {
  const cards = useStore((s) => s.cards);
  const filters = useFilters();
  const setSearch = useFilters((s) => s.setSearch);
  const toggleMulti = useFilters((s) => s.toggleMulti);
  const setTri = useFilters((s) => s.setTri);
  const clearKey = useFilters((s) => s.clearKey);
  const reset = useFilters((s) => s.reset);

  const options = useMemo(
    () => chipOptions(Object.values(cards)),
    [cards]
  );
  const activeCount = activeFilterCount(filters);

  return (
    <div
      data-testid="filter-bar"
      className="flex flex-wrap items-center gap-2 px-5 py-2 border-b border-border bg-panel/60"
    >
      <input
        id="filter-search"
        type="search"
        value={filters.search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search cards (title or id)..."
        aria-label="search cards by title or id"
        className="w-[280px] rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text placeholder:text-muted focus:border-accent focus:outline-none"
      />

      <FilterChip
        label="Project"
        selected={filters.project}
        options={options.project}
        onToggle={(v) => toggleMulti("project", v)}
        onClear={() => clearKey("project")}
      />
      <FilterChip
        label="Batch"
        selected={filters.batch}
        options={options.batch}
        onToggle={(v) => toggleMulti("batch", v)}
        onClear={() => clearKey("batch")}
      />
      <FilterChip
        label="Runner"
        selected={filters.claimedBy}
        options={options.claimedBy}
        onToggle={(v) => toggleMulti("claimedBy", v)}
        onClear={() => clearKey("claimedBy")}
      />
      <FilterChip<number>
        label="Tier"
        selected={filters.tier}
        options={options.tier}
        onToggle={(v) => toggleMulti("tier", v)}
        onClear={() => clearKey("tier")}
      />
      <FilterChip
        label="Stakes"
        selected={filters.stakes}
        options={options.stakes}
        onToggle={(v) => toggleMulti("stakes", v)}
        onClear={() => clearKey("stakes")}
      />
      <TriChip
        label="Pin"
        value={filters.pinRequired}
        onChange={(v) => setTri("pinRequired", v)}
      />
      <TriChip
        label="Thinking"
        value={filters.extendedThinking}
        onChange={(v) => setTri("extendedThinking", v)}
      />
      {options.mergeStatus.length > 0 ? (
        <FilterChip
          label="Merge"
          selected={filters.mergeStatus}
          options={options.mergeStatus}
          onToggle={(v) => toggleMulti("mergeStatus", v)}
          onClear={() => clearKey("mergeStatus")}
        />
      ) : null}

      <div className="flex-1" />
      {onOpenDepView ? (
        <button
          type="button"
          onClick={onOpenDepView}
          className="rounded border border-border bg-panel2 px-2 py-0.5 text-[11px] text-muted hover:text-text hover:border-accent/40"
          title="Show the dependency DAG of the currently visible cards"
        >
          Deps…
        </button>
      ) : null}
      <GroupByToggle />
      {activeCount > 0 ? (
        <button
          type="button"
          onClick={() => reset()}
          className="rounded-full border border-border bg-panel2 px-2 py-0.5 text-[11px] text-muted hover:text-text hover:border-accent/40"
        >
          Clear all ({activeCount})
        </button>
      ) : null}
    </div>
  );
}

function GroupByToggle() {
  const groupBy = useLens((s) => s.groupBy);
  const setGroupBy = useLens((s) => s.setGroupBy);
  return (
    <label
      className="flex items-center gap-1 rounded border border-border bg-panel2 px-1.5 py-0.5 text-[11px] text-muted"
      title="reshape the board by grouping cards within each column"
    >
      <span className="uppercase tracking-wider text-[10px] opacity-70">
        group
      </span>
      <select
        value={groupBy}
        onChange={(e) => setGroupBy(e.target.value as "none" | "project")}
        className="cursor-pointer bg-transparent pr-0.5 text-text outline-none focus:outline-none"
        aria-label="group-by lens"
      >
        <option value="none" className="bg-panel2 text-text">
          None
        </option>
        <option value="project" className="bg-panel2 text-text">
          Project
        </option>
      </select>
    </label>
  );
}
