import { useMemo, useState } from "react";

import { api, ApiError, type CardSummary } from "../lib/api";
import {
  cardPoints,
  cardProject,
  cardReady,
  cardShortId,
  cardStakes,
  cardTitle,
} from "../lib/parseCard";
import { useStore } from "../state/store";

/**
 * Backlog grooming surface (roadmap 2.3). A dense table of the backlog
 * cards with inline-editable fields (title, points, stakes) and a
 * "Ready" toggle that splits the ice-box from the sprint-ready set.
 *
 * This is the grooming surface; the kanban is the running-the-work
 * surface. Every edit writes back atomically through
 * PATCH /api/cards/:id/frontmatter, which publishes `card-updated` so
 * other connected dashboards stay in sync. Edits are optimistic with a
 * server-authoritative rollback, mirroring the grid's restake handler.
 *
 * Bulk-select / bulk-edit and "promote to sprint N" are deliberately
 * out of scope here -- they ride in on roadmap 2.8 (multi-select).
 */

type ReadyFilter = "all" | "ready" | "icebox";

const TIERS = [1, 2, 3, 4, 5, 6] as const;
const STAKES = ["low", "medium", "high"] as const;

export function Backlog() {
  const cards = useStore((s) => s.cards);
  const upsert = useStore((s) => s.upsert);
  const [filter, setFilter] = useState<ReadyFilter>("all");
  const [error, setError] = useState<string | null>(null);

  const backlog = useMemo(
    () =>
      Object.values(cards)
        .filter((c) => c.status === "backlog")
        .sort((a, b) => {
          // Ready cards float to the top (grooming order), then by id.
          const ra = cardReady(a) ? 0 : 1;
          const rb = cardReady(b) ? 0 : 1;
          if (ra !== rb) return ra - rb;
          return a.id.localeCompare(b.id);
        }),
    [cards]
  );

  const visible = useMemo(
    () =>
      backlog.filter((c) => {
        if (filter === "ready") return cardReady(c);
        if (filter === "icebox") return !cardReady(c);
        return true;
      }),
    [backlog, filter]
  );

  const readyCount = useMemo(
    () => backlog.filter((c) => cardReady(c)).length,
    [backlog]
  );

  // Optimistic write-back with server-authoritative rollback. The store
  // updates immediately; on failure we re-fetch the card (most truthful)
  // and fall back to the pre-edit snapshot only if even that fails.
  async function commitPatch(
    card: CardSummary,
    patch: Parameters<typeof api.patchCardFrontmatter>[1],
    optimistic: Record<string, unknown>
  ): Promise<void> {
    const original = card;
    upsert({
      ...original,
      frontmatter: { ...original.frontmatter, ...optimistic },
    });
    try {
      const updated = await api.patchCardFrontmatter(card.id, patch);
      upsert(updated);
      setError(null);
    } catch (err) {
      try {
        const fresh = await api.getCard(card.id);
        const { body: _body, ...summary } = fresh;
        upsert(summary);
      } catch {
        upsert(original);
      }
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div className="flex flex-col px-5 py-4">
      <div className="mb-3 flex items-center gap-3">
        <h2 className="text-sm font-semibold tracking-tight">Backlog grooming</h2>
        <FilterToggle value={filter} onChange={setFilter} />
        <div className="ml-auto text-[11px] text-muted">
          <span className="mr-3">{visible.length} shown</span>
          <span>
            {readyCount}/{backlog.length} ready
          </span>
        </div>
      </div>

      {error ? (
        <div className="mb-3 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      ) : null}

      {visible.length === 0 ? (
        <div className="rounded border border-dashed border-border bg-panel2/30 px-4 py-8 text-center text-sm text-muted">
          {backlog.length === 0
            ? "No cards in the backlog."
            : "No cards match this filter."}
        </div>
      ) : (
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="border-b border-border text-left text-[10px] uppercase tracking-wider text-muted">
              <th className="px-2 py-1.5 font-medium">Ready</th>
              <th className="px-2 py-1.5 font-medium">Card</th>
              <th className="px-2 py-1.5 font-medium">Title</th>
              <th className="px-2 py-1.5 font-medium">Pts</th>
              <th className="px-2 py-1.5 font-medium">Stakes</th>
              <th className="px-2 py-1.5 font-medium">Project</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((c) => (
              <BacklogRow key={c.id} card={c} onCommit={commitPatch} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function FilterToggle({
  value,
  onChange,
}: {
  value: ReadyFilter;
  onChange: (v: ReadyFilter) => void;
}) {
  const opts: Array<{ key: ReadyFilter; label: string }> = [
    { key: "all", label: "All" },
    { key: "ready", label: "Ready" },
    { key: "icebox", label: "Ice-box" },
  ];
  return (
    <div className="flex items-center gap-0.5 rounded border border-border bg-panel2/40 p-0.5">
      {opts.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          className={[
            "px-2 py-0.5 text-[11px] rounded transition-colors",
            value === o.key
              ? "bg-panel2 text-text border border-border"
              : "text-muted hover:text-text",
          ].join(" ")}
          aria-pressed={value === o.key}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function BacklogRow({
  card,
  onCommit,
}: {
  card: CardSummary;
  onCommit: (
    card: CardSummary,
    patch: Parameters<typeof api.patchCardFrontmatter>[1],
    optimistic: Record<string, unknown>
  ) => Promise<void>;
}) {
  const ready = cardReady(card);
  const points = cardPoints(card);
  const stakes = cardStakes(card);
  const project = cardProject(card);

  return (
    <tr className="border-b border-border/50 hover:bg-panel2/30">
      <td className="px-2 py-1.5">
        <button
          role="switch"
          aria-checked={ready}
          aria-label={`mark ${cardTitle(card)} ${ready ? "not ready" : "ready"}`}
          onClick={() =>
            void onCommit(card, { ready: !ready }, { ready: !ready })
          }
          className={[
            "h-4 w-7 rounded-full border transition-colors relative",
            ready ? "bg-accent border-accent" : "bg-panel2 border-border",
          ].join(" ")}
        >
          <span
            className={[
              "absolute top-0.5 h-3 w-3 rounded-full bg-white transition-all",
              ready ? "left-3.5" : "left-0.5",
            ].join(" ")}
          />
        </button>
      </td>
      <td className="px-2 py-1.5 font-mono text-muted whitespace-nowrap">
        {cardShortId(card)}
      </td>
      <td className="px-2 py-1.5">
        <EditableTitle card={card} onCommit={onCommit} />
      </td>
      <td className="px-2 py-1.5">
        <select
          aria-label={`points for ${cardTitle(card)}`}
          value={points ?? ""}
          onChange={(e) => {
            const v = Number(e.target.value);
            if (!Number.isFinite(v)) return;
            void onCommit(card, { points: v }, { points: v });
          }}
          className="rounded border border-border bg-panel2 px-1 py-0.5 text-[12px] text-text focus:border-accent focus:outline-none"
        >
          {points === null ? <option value="">--</option> : null}
          {TIERS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </td>
      <td className="px-2 py-1.5">
        <select
          aria-label={`stakes for ${cardTitle(card)}`}
          value={stakes ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") {
              void onCommit(card, { stakes: null }, { stakes: undefined });
              return;
            }
            void onCommit(card, { stakes: v }, { stakes: v });
          }}
          className="rounded border border-border bg-panel2 px-1 py-0.5 text-[12px] text-text focus:border-accent focus:outline-none"
        >
          <option value="">--</option>
          {STAKES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </td>
      <td className="px-2 py-1.5 max-w-[200px] truncate text-muted" title={project ?? ""}>
        {project ?? "--"}
      </td>
    </tr>
  );
}

function EditableTitle({
  card,
  onCommit,
}: {
  card: CardSummary;
  onCommit: (
    card: CardSummary,
    patch: Parameters<typeof api.patchCardFrontmatter>[1],
    optimistic: Record<string, unknown>
  ) => Promise<void>;
}) {
  const current = cardTitle(card);
  const [draft, setDraft] = useState(current);
  const [editing, setEditing] = useState(false);

  // When not editing, mirror the store value (e.g. an SSE update lands).
  const shown = editing ? draft : current;

  function commit(): void {
    setEditing(false);
    const next = draft.trim();
    if (next.length === 0 || next === current) {
      setDraft(current);
      return;
    }
    void onCommit(card, { title: next }, { title: next });
  }

  return (
    <input
      aria-label={`title for ${cardShortId(card)}`}
      value={shown}
      onFocus={() => {
        setDraft(current);
        setEditing(true);
      }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          setDraft(current);
          setEditing(false);
          e.currentTarget.blur();
        }
      }}
      className="w-full min-w-[160px] rounded border border-transparent bg-transparent px-1 py-0.5 text-[12px] text-text hover:border-border focus:border-accent focus:bg-panel2 focus:outline-none"
    />
  );
}
