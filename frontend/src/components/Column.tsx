import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useMemo, useState } from "react";

import type { CardSummary, StatusId } from "../lib/api";
import { cardCost, formatCost, type RatesPayload, rollupCost } from "../lib/cost";
import { cardPoints } from "../lib/parseCard";
import { statusDotClass } from "../lib/tierBadge";
import { type GroupBy, partitionByProject } from "../state/lens";
import {
  DEFAULT_LIMITS,
  limitStateFor,
  useWipLimits,
} from "../state/wipLimits";
import { CardTile } from "./CardTile";

interface Props {
  id: StatusId;
  label: string;
  cards: CardSummary[];
  onOpenCard: (id: string) => void;
  rates: RatesPayload;
  groupBy: GroupBy;
}

/**
 * Sort modes the column header dropdown can pick from. "rank" is the
 * default and corresponds to the manual drag-to-reorder rank persisted
 * server-side; the other modes are display-only and don't write back.
 *
 * Heartbeat is on the roadmap but defers to the agent-fleet pass --
 * the dashboard doesn't surface `claimed_by` heartbeats yet.
 */
type SortMode = "rank" | "created" | "tier" | "cost";

const SORT_LABELS: Record<SortMode, string> = {
  rank: "Rank",
  created: "Created",
  tier: "Tier",
  cost: "Cost",
};

const SORT_ORDER: SortMode[] = ["rank", "created", "tier", "cost"];

/**
 * A column of cards. Droppable via dnd-kit. Children are wrapped in a
 * SortableContext so each card is draggable.
 */
export function Column({ id, label, cards, onOpenCard, rates, groupBy }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id });
  const [sortMode, setSortMode] = useState<SortMode>("rank");
  const rollup = rollupCost(cards, rates.rates, rates.defaultInputRatio);
  const overrides = useWipLimits((s) => s.overrides);
  const limit = limitStateFor(id, cards.length, overrides);

  const sortedCards = useMemo(() => {
    if (sortMode === "rank") return cards; // already rank-sorted upstream
    const arr = [...cards];
    switch (sortMode) {
      case "created":
        arr.sort((a, b) => a.id.localeCompare(b.id));
        break;
      case "tier":
        arr.sort((a, b) => {
          const ta = cardPoints(a) ?? -Infinity;
          const tb = cardPoints(b) ?? -Infinity;
          return tb - ta; // tier descending: higher tier first
        });
        break;
      case "cost":
        arr.sort((a, b) => {
          const ca = cardCost(a, rates.rates, rates.defaultInputRatio).usd;
          const cb = cardCost(b, rates.rates, rates.defaultInputRatio).usd;
          return cb - ca; // cost descending
        });
        break;
    }
    return arr;
  }, [cards, sortMode, rates]);

  return (
    <div
      ref={setNodeRef}
      className={[
        "flex flex-col surface min-h-[calc(100vh-120px)] transition-colors",
        isOver ? "border-accent bg-accent/[0.04]" : "",
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2 px-3 py-2.5 border-b border-border">
        <span className="flex items-center gap-2 min-w-0">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(id)}`}
          />
          <span className="truncate text-[11px] font-semibold uppercase tracking-wider text-text">
            {label}
          </span>
        </span>
        <div className="flex shrink-0 items-center gap-1.5">
          {rollup.kind !== "none" ? (
            <span
              className="rounded border border-border bg-panel2 px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-muted"
              title={rollupTitle(rollup.kind, rollup.usd)}
            >
              {rollup.kind === "spent" || rollup.kind === "mixed" ? "" : "~"}
              {formatCost(rollup.usd)}
            </span>
          ) : null}
          <CountPill
            status={id}
            count={cards.length}
            limit={limit}
          />
          <SortPicker mode={sortMode} onChange={setSortMode} />
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
        <SortableContext
          items={sortedCards.map((c) => c.id)}
          strategy={verticalListSortingStrategy}
          disabled={sortMode !== "rank"}
        >
          {sortedCards.length === 0 ? (
            <div
              className={[
                "m-1 rounded border border-dashed py-10 text-center text-[11px]",
                isOver
                  ? "border-accent/60 text-accent"
                  : "border-border/70 text-muted",
              ].join(" ")}
            >
              {isOver ? "Drop to move here" : "No cards"}
            </div>
          ) : groupBy === "project" ? (
            renderGroupedByProject(sortedCards, onOpenCard, rates)
          ) : (
            sortedCards.map((c) => (
              <CardTile
                key={c.id}
                card={c}
                onOpen={onOpenCard}
                rates={rates}
              />
            ))
          )}
        </SortableContext>
      </div>
    </div>
  );
}

function CountPill({
  status,
  count,
  limit,
}: {
  status: StatusId;
  count: number;
  limit: ReturnType<typeof limitStateFor>;
}) {
  const [editing, setEditing] = useState(false);
  const setLimit = useWipLimits((s) => s.setLimit);
  const clearOverride = useWipLimits((s) => s.clearOverride);
  const [draft, setDraft] = useState<string>(
    limit ? String(limit.limit) : ""
  );

  const pillClasses = limit?.over
    ? "border-warn/60 bg-warn/15 text-warn"
    : limit?.atCap
      ? "border-accent/40 bg-accent/[0.08] text-accent"
      : "border-border bg-panel2 text-muted";

  const label = limit
    ? `${count}/${limit.limit}${limit.over ? " over" : ""}`
    : String(count);
  const title = limit
    ? limit.over
      ? `WIP limit ${limit.limit} exceeded (currently ${count}). Click to edit.`
      : `${count} of ${limit.limit} cards. Click to edit limit.`
    : "Click to set a WIP limit.";

  return (
    <span className="relative inline-block">
      <button
        type="button"
        className={`rounded-full border px-1.5 py-0.5 text-[11px] tabular-nums ${pillClasses} hover:brightness-110`}
        title={title}
        onClick={() => {
          setDraft(limit ? String(limit.limit) : "");
          setEditing((v) => !v);
        }}
      >
        {label}
      </button>
      {editing ? (
        <div
          className="absolute right-0 top-[110%] z-30 mt-1 flex items-center gap-1 rounded border border-border bg-panel2 p-1.5 text-[11px] shadow-lg"
          role="dialog"
          aria-label={`${status} WIP limit`}
        >
          <input
            type="number"
            min={0}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-16 rounded border border-border bg-panel px-1 py-0.5 text-text outline-none focus:border-accent"
            placeholder={
              DEFAULT_LIMITS[status] !== null
                ? String(DEFAULT_LIMITS[status])
                : "none"
            }
            aria-label="WIP limit"
            autoFocus
          />
          <button
            type="button"
            className="rounded border border-border bg-panel px-1.5 py-0.5 text-text hover:border-accent"
            onClick={() => {
              const n = parseInt(draft, 10);
              if (!Number.isFinite(n) || n <= 0) {
                setLimit(status, null);
              } else {
                setLimit(status, n);
              }
              setEditing(false);
            }}
          >
            Set
          </button>
          <button
            type="button"
            className="rounded border border-border bg-panel px-1.5 py-0.5 text-muted hover:text-text"
            onClick={() => {
              clearOverride(status);
              setEditing(false);
            }}
            title="Restore the column default"
          >
            Reset
          </button>
          <button
            type="button"
            className="rounded border border-border bg-panel px-1.5 py-0.5 text-muted hover:text-text"
            onClick={() => setEditing(false)}
            aria-label="cancel"
          >
            ×
          </button>
        </div>
      ) : null}
    </span>
  );
}

function SortPicker({
  mode,
  onChange,
}: {
  mode: SortMode;
  onChange: (m: SortMode) => void;
}) {
  return (
    <label
      className="flex items-center gap-1 rounded border border-border bg-panel2 px-1 py-0.5 text-[10px] text-muted"
      title="sort cards within this column"
    >
      <span className="uppercase tracking-wider opacity-70">sort</span>
      <select
        value={mode}
        onChange={(e) => onChange(e.target.value as SortMode)}
        className="bg-transparent text-text outline-none focus:outline-none cursor-pointer pr-0.5"
        aria-label="column sort mode"
      >
        {SORT_ORDER.map((m) => (
          <option key={m} value={m} className="bg-panel2 text-text">
            {SORT_LABELS[m]}
          </option>
        ))}
      </select>
    </label>
  );
}

function renderGroupedByProject(
  cards: CardSummary[],
  onOpenCard: (id: string) => void,
  rates: RatesPayload
) {
  const groups = partitionByProject(cards);
  return groups.map((g, i) => (
    <div key={g.key} className={i === 0 ? "" : "mt-1"}>
      <div className="flex items-center gap-1.5 px-1 pb-1 pt-0.5">
        <span className="h-px flex-1 bg-border/60" aria-hidden />
        <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
          {g.label}
        </span>
        <span className="rounded-full border border-border bg-panel2 px-1.5 py-0.5 text-[10px] tabular-nums text-muted">
          {g.cards.length}
        </span>
        <span className="h-px flex-1 bg-border/60" aria-hidden />
      </div>
      <div className="flex flex-col gap-2">
        {g.cards.map((c) => (
          <CardTile key={c.id} card={c} onOpen={onOpenCard} rates={rates} />
        ))}
      </div>
    </div>
  ));
}

function rollupTitle(
  kind: "est" | "spent" | "mixed" | "none",
  usd: number
): string {
  const total = `$${usd.toFixed(2)}`;
  switch (kind) {
    case "est":
      return `column estimate: ${total}`;
    case "spent":
      return `column spent: ${total}`;
    case "mixed":
      return `column total (mixed estimate + spent): ${total}`;
    case "none":
    default:
      return total;
  }
}
