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
import { api, ApiError, type Column as ColumnDef, type StatusId } from "../lib/api";
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
}

/**
 * The kanban. Columns come from the API so the backend stays the source
 * of truth for what statuses exist; we keep a fallback list so the page
 * renders even if /api/columns hiccups.
 */
export function Kanban({ loading, error }: Props) {
  const cards = useStore((s) => s.cards);
  const hydrated = useStore((s) => s.hydrated);
  const optimisticMove = useStore((s) => s.optimisticMove);
  const markInFlight = useStore((s) => s.markInFlight);

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
    const acc: Record<StatusId, ReturnType<typeof selectCardsByStatus>> = {
      backlog: [],
      active: [],
      awaiting_amendment_review: [],
      done: [],
      blocked: [],
    };
    for (const c of Object.values(cards)) {
      acc[c.status].push(c);
    }
    for (const k of Object.keys(acc) as StatusId[]) {
      acc[k].sort((a, b) => a.id.localeCompare(b.id));
    }
    return acc;
  }, [cards]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  );

  const onDragEnd = async (e: DragEndEvent): Promise<void> => {
    if (!e.over) return;
    const cardId = String(e.active.id);
    const targetStatus = e.over.id as StatusId;
    if (!isStatusId(targetStatus)) return;

    const card = cards[cardId];
    if (!card || card.status === targetStatus) return;

    // Optimistic UI: move the card in the store immediately, then call
    // the API. SSE will echo back the canonical state; the store
    // reconciles by id.
    optimisticMove(cardId, targetStatus);
    markInFlight(cardId, true);
    try {
      await api.moveCard(cardId, targetStatus);
      setMoveError(null);
    } catch (err) {
      // Roll back. Refetching the canonical card is the safest path; we
      // could also flip the store back to `card.status`, but the API
      // round-trip removes any ambiguity if the failure was partial.
      try {
        const fresh = await api.getCard(cardId);
        useStore.getState().upsert(fresh);
      } catch {
        optimisticMove(cardId, card.status);
      }
      setMoveError(err instanceof ApiError ? err.message : String(err));
    } finally {
      markInFlight(cardId, false);
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
