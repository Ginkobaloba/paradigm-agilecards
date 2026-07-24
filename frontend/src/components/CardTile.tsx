import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useState } from "react";

import type { CardSummary } from "../lib/api";
import {
  cardCost,
  formatCost,
  type CostLevel,
  type RatesPayload,
} from "../lib/cost";
import {
  cardExtendedThinking,
  cardModel,
  cardPinRequired,
  cardPoints,
  cardShortId,
  cardStakes,
  cardTitle,
} from "../lib/parseCard";
import { relativeTime } from "../lib/relativeTime";
import { selectUnmetDeps, useStore } from "../state/store";
import { useShallow } from "zustand/react/shallow";
import { stakesBadgeClass, tierBadgeClass } from "../lib/tierBadge";

interface Props {
  card: CardSummary;
  onOpen: (id: string) => void;
  rates: RatesPayload;
}

/**
 * A single card on a column. Draggable via dnd-kit. Clicking opens the
 * detail modal. Dense by design -- the tile carries just enough to
 * triage at a glance; the full frontmatter lives in the modal.
 */
export function CardTile({ card, onOpen, rates }: Props) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: card.id });
  // selectUnmetDeps returns a fresh object each call, so selecting it raw
  // gives useSyncExternalStore a new snapshot every render and React 18.3
  // throws "getSnapshot should be cached" (blanking the tile). useShallow
  // compares the {count, firstUnmetId} result by value and keeps it stable.
  const unmet = useStore(useShallow((s) => selectUnmetDeps(s, card)));
  const [copied, setCopied] = useState(false);

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
  const cost = cardCost(card, rates.rates, rates.defaultInputRatio);
  // Age is only interesting while a card is alive; once it's done we
  // stop caring how long ago the runner stamped the file.
  const isActive = card.status === "active";
  const isDone = card.status === "done";
  const age = relativeTime(card.mtimeMs, {
    isStaleEligible: isActive,
  });

  const handleCopy = async (e: React.PointerEvent | React.MouseEvent): Promise<void> => {
    // Stop the tile's onClick from opening the modal, and stop the
    // pointer-down from starting a drag in dnd-kit.
    e.stopPropagation();
    e.preventDefault();
    try {
      await navigator.clipboard.writeText(card.id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // Best-effort: clipboard may be denied in insecure contexts. Fall
      // through silently; the tooltip still tells the user what would
      // be copied if it worked.
    }
  };

  const handleDepClick = (e: React.MouseEvent): void => {
    e.stopPropagation();
    if (unmet.firstUnmetId) onOpen(unmet.firstUnmetId);
  };

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
        <button
          type="button"
          onClick={handleCopy}
          onPointerDown={(e) => e.stopPropagation()}
          className={[
            "group flex items-center gap-1 rounded font-mono text-[11px]",
            "px-1 -mx-1 py-0 transition-colors",
            copied
              ? "text-ok"
              : "text-muted hover:text-text hover:bg-panel2",
          ].join(" ")}
          title={copied ? "copied" : `click to copy ${card.id}`}
          aria-label={`copy card id ${card.id}`}
        >
          <span>{shortId}</span>
          <span
            className={[
              "text-[9px] uppercase tracking-wider",
              copied
                ? "opacity-100"
                : "opacity-0 group-hover:opacity-60",
            ].join(" ")}
          >
            {copied ? "copied" : "copy"}
          </span>
        </button>
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
        {unmet.count > 0 ? (
          <button
            type="button"
            onClick={handleDepClick}
            onPointerDown={(e) => e.stopPropagation()}
            className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] font-medium text-warn hover:bg-warn/20"
            title={`blocked on ${unmet.count} unfinished ${
              unmet.count === 1 ? "dep" : "deps"
            }${unmet.firstUnmetId ? ` — click to open ${unmet.firstUnmetId}` : ""}`}
            aria-label={`blocked on ${unmet.count} dependencies`}
          >
            blocked on {unmet.count}
          </button>
        ) : null}
        {cost.kind !== "none" ? (
          <span
            className={[
              "rounded border px-1.5 py-0.5 text-[10px] font-mono tabular-nums",
              costChipClass(cost.level),
            ].join(" ")}
            title={costChipTitle(cost.usd, cost.kind, cost.cap, cost.model)}
          >
            {cost.kind === "spent" ? "" : "~"}
            {formatCost(cost.usd)}
          </span>
        ) : null}
        {age && !isDone ? (
          <span
            className={[
              "ml-auto font-mono text-[10px] tabular-nums",
              age.stale ? "text-warn" : "text-muted",
            ].join(" ")}
            title={`updated ${age.label} ago${age.stale ? " — stale" : ""}`}
          >
            {age.stale ? `stale ${age.label}` : age.label}
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

function costChipClass(level: CostLevel): string {
  switch (level) {
    case "danger":
      return "text-danger border-danger/40 bg-danger/10";
    case "warn":
      return "text-warn border-warn/40 bg-warn/10";
    case "ok":
    default:
      return "text-muted border-border bg-panel";
  }
}

function costChipTitle(
  usd: number,
  kind: "est" | "spent" | "none",
  cap: number | null,
  model: string | null
): string {
  const label =
    kind === "spent"
      ? "actual cost"
      : kind === "est"
        ? "estimated cost"
        : "cost";
  const modelStr = model ? ` (${model})` : "";
  const capStr =
    cap !== null && cap > 0 ? `, cap $${cap.toFixed(2)}` : "";
  return `${label}${modelStr}: $${usd.toFixed(4)}${capStr}`;
}
