/**
 * Sprint detail / planner surface.
 *
 *   - Header: sprint name + dates + goal + status badge + Edit button.
 *   - Member list: cards already assigned to the sprint, with their
 *     status dot, short id, title, planned points, remove button.
 *   - Backlog picker: a side panel that lists kanban-backlog cards not
 *     already in this sprint; click a card to add it (plannedPoints
 *     defaults to the card's points field), shift-click queues many.
 *
 * Capacity meters (target vs. assigned points / cost / review hours)
 * arrive in the next PR (2.2). This surface lays the assignment side
 * so the meters have data to consume.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CapacityPanel } from "../components/CapacityPanel";
import { CardModal } from "../components/CardModal";
import { SprintFormDialog } from "../components/SprintFormDialog";
import { useRates } from "../hooks/useRates";
import { useAuth } from "../hooks/useAuth";
import {
  ApiError,
  type CardSummary,
  type Sprint,
  type SprintCardLink,
  type SprintPatch,
  type SprintStatus,
  sprintsApi,
} from "../lib/api";
import {
  cardPoints,
  cardShortId,
  cardTitle,
} from "../lib/parseCard";
import { computeSprintCapacity } from "../lib/sprintCapacity";
import { statusDotClass } from "../lib/tierBadge";
import { useStore } from "../state/store";

export function SprintDetail() {
  const params = useParams<{ id: string }>();
  const sprintId = Number.parseInt(params.id ?? "", 10);
  const validId = Number.isFinite(sprintId);

  const [sprint, setSprint] = useState<Sprint | null>(null);
  const [members, setMembers] = useState<SprintCardLink[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [openCard, setOpenCard] = useState<string | null>(null);
  const cards = useStore((s) => s.cards);
  const { isAuthed } = useAuth();
  const rates = useRates(isAuthed);

  const capacity = useMemo(
    () =>
      sprint && members
        ? computeSprintCapacity(sprint, members, cards, rates.rates, rates.defaultInputRatio)
        : null,
    [sprint, members, cards, rates]
  );

  const load = useCallback(
    async (signal?: AbortSignal): Promise<void> => {
      if (!validId) return;
      try {
        const res = await sprintsApi.get(sprintId);
        if (signal?.aborted) return;
        setSprint(res.sprint);
        setMembers(res.cards);
        setError(null);
      } catch (err: unknown) {
        if (signal?.aborted) return;
        setError(err instanceof ApiError ? err.message : String(err));
      }
    },
    [sprintId, validId]
  );

  useEffect(() => {
    const ac = new AbortController();
    void load(ac.signal);
    return () => ac.abort();
  }, [load]);

  const onEdit = async (patch: SprintPatch): Promise<void> => {
    await sprintsApi.patch(sprintId, patch);
    setEditOpen(false);
    await load();
  };

  const onAddCard = async (cardId: string): Promise<void> => {
    const c = cards[cardId];
    const pp = c ? cardPoints(c) ?? null : null;
    try {
      await sprintsApi.addCard(sprintId, cardId, pp);
      await load();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const onRemoveCard = async (cardId: string): Promise<void> => {
    try {
      await sprintsApi.removeCard(sprintId, cardId);
      await load();
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  if (!validId) {
    return (
      <div className="px-5 py-6">
        <div className="surface p-4 text-xs text-danger">
          Invalid sprint id.
        </div>
      </div>
    );
  }

  if (error && !sprint) {
    return (
      <div className="px-5 py-6">
        <div className="surface p-4 text-xs text-danger">{error}</div>
        <div className="mt-2 text-xs">
          <Link to="/sprints" className="text-accent hover:underline">
            ← Back to sprints
          </Link>
        </div>
      </div>
    );
  }

  if (!sprint || members === null) {
    return (
      <div className="px-5 py-6">
        <div className="surface p-4 text-xs text-muted italic">Loading…</div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 px-5 py-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <Link
            to="/sprints"
            className="text-[11px] text-muted hover:text-text"
          >
            ← Sprints
          </Link>
          <span className="text-muted">/</span>
          <h1 className="text-sm font-semibold text-text truncate">
            {sprint.name}
          </h1>
          <StatusBadge status={sprint.status} />
        </div>
        <button
          type="button"
          onClick={() => setEditOpen(true)}
          className="rounded border border-border bg-panel2 px-2 py-1 text-[11px] text-muted hover:text-text hover:border-accent/40"
        >
          Edit
        </button>
      </div>

      {error ? (
        <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}

      <div className="surface p-3 text-[11px] text-muted">
        <div>
          <span className="text-text">
            {formatDateRange(sprint.startsAt, sprint.endsAt)}
          </span>
        </div>
        {sprint.goal ? (
          <div className="mt-1 text-text/90 italic">{sprint.goal}</div>
        ) : (
          <div className="mt-1 italic">No goal set.</div>
        )}
      </div>

      {capacity ? (
        <CapacityPanel
          capacity={capacity}
          onEditTargets={() => setEditOpen(true)}
        />
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.6fr_1fr]">
        <MemberList
          members={members}
          cards={cards}
          onOpen={(id) => setOpenCard(id)}
          onRemove={(id) => void onRemoveCard(id)}
        />
        <BacklogPicker
          memberIds={new Set(members.map((m) => m.cardId))}
          cards={cards}
          onAdd={(id) => void onAddCard(id)}
        />
      </div>

      <SprintFormDialog
        open={editOpen}
        onClose={() => setEditOpen(false)}
        onSubmit={onEdit}
        mode="edit"
        sprint={sprint}
      />
      <CardModal cardId={openCard} onClose={() => setOpenCard(null)} />
    </div>
  );
}

function MemberList({
  members,
  cards,
  onOpen,
  onRemove,
}: {
  members: readonly SprintCardLink[];
  cards: Record<string, CardSummary>;
  onOpen: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <section className="surface flex flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-text">
          Members ({members.length})
        </h2>
      </div>
      {members.length === 0 ? (
        <div className="px-3 py-6 text-xs text-muted italic">
          No cards assigned. Pick from the backlog on the right →
        </div>
      ) : (
        <ul className="flex flex-col divide-y divide-border/60">
          {members.map((m) => {
            const c = cards[m.cardId];
            return (
              <li
                key={m.cardId}
                className="flex items-center gap-2 px-3 py-2 hover:bg-panel/40"
              >
                {c ? (
                  <button
                    type="button"
                    onClick={() => onOpen(c.id)}
                    className="flex flex-1 items-center gap-2 text-left"
                  >
                    <span
                      className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(c.status)}`}
                      aria-hidden
                    />
                    <span className="font-mono text-[10px] text-muted">
                      {cardShortId(c)}
                    </span>
                    <span className="text-[12px] text-text truncate">
                      {cardTitle(c)}
                    </span>
                  </button>
                ) : (
                  <span className="flex-1 font-mono text-[11px] text-muted italic">
                    {m.cardId} (missing)
                  </span>
                )}
                <span
                  className="rounded border border-border bg-panel2 px-1.5 py-0.5 text-[10px] tabular-nums text-muted"
                  title="planned points"
                >
                  {m.plannedPoints ?? "—"} pts
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(m.cardId)}
                  className="rounded border border-border bg-panel2 px-1.5 py-0.5 text-[10px] text-muted hover:border-danger/40 hover:text-danger"
                  aria-label={`Remove ${m.cardId} from sprint`}
                  title="Remove from sprint"
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function BacklogPicker({
  memberIds,
  cards,
  onAdd,
}: {
  memberIds: ReadonlySet<string>;
  cards: Record<string, CardSummary>;
  onAdd: (id: string) => void;
}) {
  const [search, setSearch] = useState("");

  const candidates = useMemo(() => {
    const all = Object.values(cards).filter(
      (c) => c.status === "backlog" && !memberIds.has(c.id)
    );
    if (search.trim().length === 0) {
      return all.slice(0, 50);
    }
    const q = search.trim().toLowerCase();
    return all
      .filter((c) => {
        const t = cardTitle(c).toLowerCase();
        return t.includes(q) || c.id.toLowerCase().includes(q);
      })
      .slice(0, 50);
  }, [cards, memberIds, search]);

  return (
    <section className="surface flex flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-text">
          Backlog
        </h2>
        <span className="text-[10px] text-muted">{candidates.length}</span>
      </div>
      <div className="border-b border-border px-3 py-2">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search backlog cards…"
          aria-label="search backlog"
          className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[11px] text-text focus:border-accent focus:outline-none"
        />
      </div>
      {candidates.length === 0 ? (
        <div className="px-3 py-6 text-xs text-muted italic">
          {search.trim()
            ? "No backlog cards match that search."
            : "No backlog cards available to assign."}
        </div>
      ) : (
        <ul className="flex max-h-[60vh] flex-col divide-y divide-border/60 overflow-y-auto">
          {candidates.map((c) => (
            <li
              key={c.id}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-panel/40"
            >
              <span className="flex-1 min-w-0">
                <span className="block font-mono text-[10px] text-muted">
                  {cardShortId(c)}
                </span>
                <span className="block text-[11px] text-text truncate">
                  {cardTitle(c)}
                </span>
              </span>
              <button
                type="button"
                onClick={() => onAdd(c.id)}
                className="rounded border border-accent/40 bg-accent/10 px-1.5 py-0.5 text-[10px] font-semibold text-accent hover:bg-accent/20"
              >
                + add
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StatusBadge({ status }: { status: SprintStatus }) {
  const tone = STATUS_TONE[status];
  return (
    <span
      className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${tone}`}
    >
      {status}
    </span>
  );
}

const STATUS_TONE: Record<SprintStatus, string> = {
  planning: "border-muted/50 bg-panel2 text-muted",
  active: "border-accent/60 bg-accent/[0.08] text-accent",
  completed: "border-ok/40 bg-ok/[0.08] text-ok",
  cancelled: "border-danger/40 bg-danger/[0.08] text-danger",
};

function formatDateRange(start: string, end: string): string {
  return `${start.slice(0, 10)} → ${end.slice(0, 10)}`;
}
