import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";

import type { CardSummary, StatusId } from "../lib/api";
import { statusDotClass } from "../lib/tierBadge";
import { CardTile } from "./CardTile";

interface Props {
  id: StatusId;
  label: string;
  cards: CardSummary[];
  onOpenCard: (id: string) => void;
}

/**
 * A column of cards. Droppable via dnd-kit. Children are wrapped in a
 * SortableContext so each card is draggable.
 */
export function Column({ id, label, cards, onOpenCard }: Props) {
  const { setNodeRef, isOver } = useDroppable({ id });

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
        <span className="shrink-0 rounded-full border border-border bg-panel2 px-1.5 py-0.5 text-[11px] tabular-nums text-muted">
          {cards.length}
        </span>
      </div>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
        <SortableContext
          items={cards.map((c) => c.id)}
          strategy={verticalListSortingStrategy}
        >
          {cards.length === 0 ? (
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
          ) : (
            cards.map((c) => (
              <CardTile key={c.id} card={c} onOpen={onOpenCard} />
            ))
          )}
        </SortableContext>
      </div>
    </div>
  );
}
