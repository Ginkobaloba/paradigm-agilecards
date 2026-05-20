import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import type { CardSummary } from "../lib/api";
import {
  cardExtendedThinking,
  cardModel,
  cardPinRequired,
  cardPoints,
  cardShortId,
  cardStakes,
  cardTitle,
} from "../lib/parseCard";
import { stakesBadgeClass, tierBadgeClass } from "../lib/tierBadge";

interface Props {
  card: CardSummary;
  onOpen: (id: string) => void;
}

/**
 * A single card on a column. Draggable via dnd-kit. Clicking opens the
 * detail modal. Dense by design -- the tile carries just enough to
 * triage at a glance; the full frontmatter lives in the modal.
 */
export function CardTile({ card, onOpen }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const title = cardTitle(card);
  const shortId = cardShortId(card);
  const points = cardPoints(card);
  const extended = cardExtendedThinking(card);
  const stakes = cardStakes(card);
  const model = cardModel(card);
  const pin = cardPinRequired(card);

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onClick={(e) => {
        // dnd-kit suppresses listeners mid-drag, so a plain click here
        // is a real click and not the tail of a drag.
        e.stopPropagation();
        onOpen(card.id);
      }}
      className={[
        "surface-2 p-3 cursor-pointer flex flex-col gap-2 transition-all",
        "hover:border-accent hover:bg-[#20262f]",
        isDragging
          ? "opacity-60 border-accent shadow-lg shadow-black/40"
          : "",
      ].join(" ")}
    >
      <div className="flex items-start gap-2.5">
        {typeof points === "number" ? (
          <span
            className={[
              "inline-flex shrink-0 items-center justify-center w-6 h-6 rounded-md",
              "text-[11px] font-semibold text-bg",
              tierBadgeClass(points),
            ].join(" ")}
            title={`tier ${points}${extended ? " - extended thinking" : ""}`}
          >
            {points}
          </span>
        ) : null}
        <span className="flex-1 text-[13px] font-medium text-text leading-snug">
          {title}
        </span>
        {pin ? (
          <span
            className="shrink-0 rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium text-warn"
            title="pin required: human approval needed to merge"
          >
            pin
          </span>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11px] text-muted">{shortId}</span>
        {stakes ? (
          <span
            className={[
              "rounded border px-1.5 py-0.5 text-[10px] font-medium capitalize",
              stakesBadgeClass(stakes),
            ].join(" ")}
          >
            {stakes}
          </span>
        ) : null}
        {extended ? (
          <span
            className="rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent"
            title="extended thinking enabled"
          >
            thinking
          </span>
        ) : null}
      </div>

      {model ? (
        <div
          className="truncate font-mono text-[11px] text-muted"
          title={model}
        >
          {model}
        </div>
      ) : null}
    </div>
  );
}
