import {
  DndContext,
  type DragEndEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { CardModal } from "../components/CardModal";
import { Column } from "../components/Column";
import {
  api,
  ApiError,
  type CardSummary,
  type Column as ColumnDef,
  type StatusId,
} from "../lib/api";
import type { RatesPayload } from "../lib/cost";
import { selectCardsByStatus, useStore } from "../state/store";

const COLUMN_FALLBACK: ColumnDef[] = [
  { id: "backlog", label: "Backlog" },
  { id: "active", label: "Active" },
  { id: "awaiting_amendment_review", label: "In Review" },
  { id: "done", label: "Done" },
  { id: "blocked", label: "Blocked" },
];

interface Props {
  loading: boolean;
  error: string | null;
  rates: RatesPayload;
}

/**
 * The kanban. Columns come from the API so the backend stays the source
 * of truth for what statuses exist; we keep a fallback list so the page
 * renders even if /api/columns hiccups.
 */
export function Kanban({ loading, error, rates }: Props) {
  const cards = useStore((s) => s.cards);
  const hydrated = useStore((s) => s.hydrated);
  const optimisticMove = useStore((s) => s.optimisticMove);
  const markInFlight = useStore((s) => s.markInFlight);
  const patchRank = useStore((s) => s.patchRank);

  const [columns, setColumns] = useState<ColumnDef[]>(COLUMN_FALLBACK);
  const [openCard, setOpenCard] = useState<string | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .listColumns()
      .then((r) => setColumns(r.columns))
      .catch(() => {
        /* fallback is fine */
      });
  }, []);

  const cardsByStatus = useMemo(() => {
    const acc: Record<StatusId, CardSummary[]> = {
      backlog: [],
      active: [],
      awaiting_amendment_review: [],
      done: [],
      blocked: [],
    };
    // Use the store's selector so the rank-aware ordering matches what
    // the columns render.
    const state = useStore.getState();
    for (const s of Object.keys(acc) as StatusId[]) {
      acc[s] = selectCardsByStatus(state, s);
    }
    return acc;
    // `cards` is intentionally a dep so a card change triggers re-sort.
    // We also re-run on rank state changes via the same channel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cards, useStore((s) => s.ranks)]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  );

  const onDragEnd = async (e: DragEndEvent): Promise<void> => {
    if (!e.over) return;
    const cardId = String(e.active.id);
    const overId = String(e.over.id);

    const active = cards[cardId];
    if (!active) return;

    // Two arms based on what we dropped *on*:
    //   - a column id (status) -> empty drop zone in that column
    //   - a card id            -> dropped on/near another card

    if (isStatusId(overId)) {
      if (active.status === overId) return; // same-column empty-area: no-op
      await runMove(active, overId);
      return;
    }

    const overCard = cards[overId];
    if (!overCard) return;

    if (overCard.status !== active.status) {
      // Cross-column drop onto another card.
      await runMove(active, overCard.status);
      return;
    }

    // Same-column reorder. Work out the new neighbor ids and POST a
    // rank update. Determine direction by comparing original indices in
    // the sorted column.
    const column = cardsByStatus[active.status];
    const activeIdx = column.findIndex((c) => c.id === active.id);
    const overIdx = column.findIndex((c) => c.id === overCard.id);
    if (activeIdx < 0 || overIdx < 0 || activeIdx === overIdx) return;

    const movingDown = activeIdx < overIdx;
    let prevId: string | null;
    let nextId: string | null;
    if (movingDown) {
      // Land after over.
      prevId = overCard.id;
      nextId = column[overIdx + 1]?.id ?? null;
    } else {
      // Land before over.
      prevId = column[overIdx - 1]?.id ?? null;
      nextId = overCard.id;
    }

    try {
      const res = await api.setRank(active.id, active.status, prevId, nextId);
      patchRank(active.id, active.status, res.rank);
      setMoveError(null);
    } catch (err) {
      setMoveError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const runMove = async (
    card: CardSummary,
    targetStatus: StatusId
  ): Promise<void> => {
    optimisticMove(card.id, targetStatus);
    markInFlight(card.id, true);
    try {
      const res = await api.moveCard(card.id, targetStatus);
      if (typeof res.rank === "number") {
        patchRank(card.id, targetStatus, res.rank);
      }
      setMoveError(null);
    } catch (err) {
      // Roll back. Refetching the canonical card is the safest path; we
      // could also flip the store back to `card.status`, but the API
      // round-trip removes any ambiguity if the failure was partial.
      try {
        const fresh = await api.getCard(card.id);
        useStore.getState().upsert(fresh);
      } catch {
        optimisticMove(card.id, card.status);
      }
      setMoveError(err instanceof ApiError ? err.message : String(err));
    } finally {
      markInFlight(card.id, false);
    }
  };

  // Before the first successful load, show a skeleton instead of five
  // empty columns. A hard load failure gets a dedicated error panel.
  const showSkeleton = !hydrated && error === null;
  const gridStyle = {
    gridTemplateColumns: `repeat(${columns.length}, minmax(260px, 1fr))`,
  };

  return (
    <div className="flex flex-col gap-3 px-5 py-4">
      {hydrated && error ? <Banner>{error}</Banner> : null}
      {moveError ? <Banner>move failed: {moveError}</Banner> : null}

      {!hydrated && error ? (
        <ErrorState message={error} />
      ) : (
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          <div className="grid gap-3" style={gridStyle}>
            {columns.map((c) =>
              showSkeleton ? (
                <SkeletonColumn key={c.id} label={c.label} />
              ) : (
                <Column
                  key={c.id}
                  id={c.id}
                  label={c.label}
                  cards={cardsByStatus[c.id] ?? []}
                  onOpenCard={(id) => setOpenCard(id)}
                  rates={rates}
                />
              )
            )}
          </div>
        </DndContext>
      )}

      <CardModal cardId={openCard} onClose={() => setOpenCard(null)} />
    </div>
  );
}

/** A compact inline notice for non-fatal errors (e.g. a failed move). */
function Banner({ children }: { children: ReactNode }) {
  return (
    <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
      {children}
    </div>
  );
}

/** Shown when the board can't load at all -- bad token, backend down. */
function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="surface flex w-full max-w-md flex-col gap-2 p-6 text-center">
        <h2 className="text-sm font-semibold text-text">
          Couldn't load the board
        </h2>
        <p className="break-words font-mono text-xs text-danger">{message}</p>
        <p className="text-xs text-muted">
          Check that the backend is reachable and your token is still valid,
          then hit Refresh.
        </p>
      </div>
    </div>
  );
}

/** Placeholder column shown while the first card load is in flight. */
function SkeletonColumn({ label }: { label: string }) {
  return (
    <div className="flex min-h-[calc(100vh-120px)] flex-col surface">
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted">
          {label}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-2">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="surface-2 flex animate-pulse flex-col gap-2.5 p-3"
          >
            <div className="flex gap-2.5">
              <div className="h-6 w-6 shrink-0 rounded-md bg-border" />
              <div className="flex flex-1 flex-col gap-1.5 pt-0.5">
                <div className="h-2.5 w-5/6 rounded bg-border" />
                <div className="h-2.5 w-1/2 rounded bg-border/60" />
              </div>
            </div>
            <div className="h-2.5 w-2/3 rounded bg-border/50" />
          </div>
        ))}
      </div>
    </div>
  );
}

function isStatusId(v: unknown): v is StatusId {
  return (
    v === "backlog" ||
    v === "active" ||
    v === "awaiting_amendment_review" ||
    v === "done" ||
    v === "blocked"
  );
}
