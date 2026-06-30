/**
 * Modal showing the dependency DAG of the currently-visible (filtered)
 * card set. Cards lay out as columns by depth -- depth 0 (no upstream
 * deps inside the visible set) on the left, downstream cards to the
 * right. Cycles are flagged in warn color so they're impossible to miss.
 *
 * Click any card to open its detail modal; the dep view stays open
 * underneath so the operator can flip through several cards quickly.
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useMemo } from "react";

import type { CardSummary } from "../lib/api";
import {
  computeDepLayout,
  countDependents,
  type DepNode,
} from "../lib/depGraph";
import { cardShortId, cardTitle } from "../lib/parseCard";
import { statusDotClass } from "../lib/tierBadge";

interface Props {
  open: boolean;
  onClose: () => void;
  cards: readonly CardSummary[];
  onOpenCard: (id: string) => void;
}

export function DependencyView({ open, onClose, cards, onOpenCard }: Props) {
  const layout = useMemo(() => computeDepLayout(cards), [cards]);
  const totalCount = cards.length;
  const cycleCount = layout.cycleIds.size;

  // "which card unblocks the most?" -- precompute the dependent count
  // for every node once, so the cells render in O(1).
  const dependents = useMemo(() => {
    const m = new Map<string, number>();
    for (const node of layout.nodes.values()) {
      m.set(node.card.id, countDependents(node.card.id, layout));
    }
    return m;
  }, [layout]);

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className="surface fixed left-1/2 top-[5vh] z-50 flex max-h-[90vh] w-[min(1200px,95vw)] -translate-x-1/2 flex-col"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <Dialog.Title className="text-sm font-semibold text-text">
              Dependencies
              <span className="ml-2 text-muted">
                ({totalCount} card{totalCount === 1 ? "" : "s"}
                {cycleCount > 0
                  ? `, ${cycleCount} in cycle${cycleCount === 1 ? "" : "s"}`
                  : ""})
              </span>
            </Dialog.Title>
            <Dialog.Close className="btn" aria-label="Close dep view">
              Close
            </Dialog.Close>
          </div>
          <div className="flex-1 overflow-auto p-3">
            {totalCount === 0 ? (
              <div className="text-muted text-sm italic">
                No cards visible. Adjust filters to populate the graph.
              </div>
            ) : (
              <div className="flex items-stretch gap-3">
                {layout.columns.map((col, i) => (
                  <DepColumn
                    key={i}
                    depth={i}
                    nodes={col}
                    dependents={dependents}
                    onOpenCard={(id) => onOpenCard(id)}
                  />
                ))}
              </div>
            )}
          </div>
          <Legend />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function DepColumn({
  depth,
  nodes,
  dependents,
  onOpenCard,
}: {
  depth: number;
  nodes: readonly DepNode[];
  dependents: ReadonlyMap<string, number>;
  onOpenCard: (id: string) => void;
}) {
  return (
    <div className="flex w-[240px] shrink-0 flex-col gap-2">
      <div className="flex items-center justify-between px-1 text-[10px] font-semibold uppercase tracking-wider text-muted">
        <span>
          {depth === 0 ? "Ready" : `Depth ${depth}`}
        </span>
        <span className="rounded-full border border-border bg-panel2 px-1.5 py-0.5 tabular-nums">
          {nodes.length}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {nodes.map((n) => (
          <DepCell
            key={n.card.id}
            node={n}
            dependents={dependents.get(n.card.id) ?? 0}
            onOpen={() => onOpenCard(n.card.id)}
          />
        ))}
      </div>
    </div>
  );
}

function DepCell({
  node,
  dependents,
  onOpen,
}: {
  node: DepNode;
  dependents: number;
  onOpen: () => void;
}) {
  const { card, visibleDeps, externalDeps, inCycle } = node;
  const tone = inCycle
    ? "border-warn/60 bg-warn/[0.08]"
    : "border-border bg-panel2";

  return (
    <button
      type="button"
      onClick={onOpen}
      className={`flex flex-col gap-1 rounded border px-2 py-1.5 text-left text-[11px] hover:border-accent/60 ${tone}`}
      title={
        inCycle
          ? "This card is in a dependency cycle. The runner can never start it as-is."
          : `Depth ${node.depth}`
      }
    >
      <div className="flex items-center gap-1.5">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(card.status)}`}
          aria-hidden
        />
        <span className="font-mono text-[10px] text-muted">
          {cardShortId(card)}
        </span>
        {inCycle ? (
          <span className="rounded border border-warn/40 px-1 text-[9px] uppercase tracking-wider text-warn">
            cycle
          </span>
        ) : null}
      </div>
      <div className="line-clamp-2 text-text">{cardTitle(card)}</div>
      <div className="flex items-center gap-2 text-[10px] text-muted">
        {visibleDeps.length > 0 ? (
          <span title="upstream deps inside the visible set">
            <span aria-hidden>↑</span> {visibleDeps.length}
          </span>
        ) : null}
        {externalDeps.length > 0 ? (
          <span title="dependencies on cards outside the visible filter">
            ext {externalDeps.length}
          </span>
        ) : null}
        {dependents > 0 ? (
          <span title="cards (transitively) blocked by this one">
            unblocks {dependents}
          </span>
        ) : null}
      </div>
    </button>
  );
}

function Legend() {
  return (
    <div className="flex items-center gap-4 border-t border-border px-4 py-2 text-[10px] text-muted">
      <span>
        <span className="font-semibold text-text">Ready</span> = no
        upstream deps inside the visible set; can start now.
      </span>
      <span>
        <span className="text-warn">cycle</span> = card is part of a
        dependency loop and cannot be started.
      </span>
      <span>
        <span className="font-semibold text-text">unblocks N</span> =
        cards (transitively) blocked by this one.
      </span>
    </div>
  );
}
