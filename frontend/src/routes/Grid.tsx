import {
  DndContext,
  type DragEndEvent,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useMemo, useRef, useState, type CSSProperties } from "react";

import { CardModal } from "../components/CardModal";
import { FilterBar } from "../components/FilterBar";
import { api, ApiError, type CardSummary } from "../lib/api";
import { formatCost, type RatesPayload } from "../lib/cost";
import {
  AXIS_OPTIONS,
  type AxisKey,
  axisValue,
  classifyQuadrant,
  defaultScaleFor,
  normalize,
  projectColor,
  QUADRANT_LABEL,
  restakeFromDrag,
  STAKES_ORDER,
} from "../lib/gridLayout";
import { cardMatchesFilters, useFilters } from "../state/filters";
import { useGridAxes } from "../state/gridAxes";
import { projectKeyOf } from "../state/lens";
import { cardTitle, cardShortId } from "../lib/parseCard";
import { useStore } from "../state/store";

interface Props {
  rates: RatesPayload;
}

const PLOT_ID = "grid-plot";

/**
 * Dual-axis spend-side optimizer view. Plots each card on a normalized
 * (x, y) plane with configurable axes and a four-quadrant overlay so
 * the operator can see at a glance which work is worth doing.
 *
 * v1 makes the Y axis drag-editable when it's set to "stakes" -- the
 * drag handler snaps the drop position to a stakes bucket and PATCHes
 * the card's frontmatter. Other axes are read-only in v1 (cost cap
 * drag-edit is deferred until we visualize cap as distinct from the
 * already-shown actual cost).
 *
 * The filter chip bar above the plot is the same one the kanban uses,
 * so the same filter set carries between views without ceremony.
 */
export function Grid({ rates }: Props) {
  const cards = useStore((s) => s.cards);
  const upsert = useStore((s) => s.upsert);
  const filters = useFilters();

  // Axes live in a store (not local state) so a saved view can capture
  // and restore them; see state/gridAxes.ts and state/savedView.ts.
  const xAxis = useGridAxes((s) => s.xAxis);
  const yAxis = useGridAxes((s) => s.yAxis);
  const setXAxis = useGridAxes((s) => s.setXAxis);
  const setYAxis = useGridAxes((s) => s.setYAxis);
  const [openCard, setOpenCard] = useState<string | null>(null);
  const [patchError, setPatchError] = useState<string | null>(null);

  // Subset of cards after filter chips. Drives both the plotted set
  // and the "ungraphed" tray (anything missing a value on either axis).
  const visible = useMemo(
    () => Object.values(cards).filter((c) => cardMatchesFilters(c, filters)),
    [cards, filters]
  );

  const rows = useMemo(() => {
    const xVals = visible.map((c) => axisValue(c, xAxis, rates));
    const yVals = visible.map((c) => axisValue(c, yAxis, rates));
    const xNorm = normalize(xVals, defaultScaleFor(xAxis));
    const yNorm = normalize(yVals, defaultScaleFor(yAxis));
    return visible.map((c, i) => ({
      card: c,
      xRaw: xVals[i] ?? null,
      yRaw: yVals[i] ?? null,
      xNorm: xNorm[i] ?? null,
      yNorm: yNorm[i] ?? null,
    }));
  }, [visible, xAxis, yAxis, rates]);

  const plotted = rows.filter(
    (r): r is typeof r & { xNorm: number; yNorm: number } =>
      r.xNorm !== null && r.yNorm !== null
  );
  const ungraphed = rows.filter((r) => r.xNorm === null || r.yNorm === null);

  const yEditable = yAxis === "stakes";

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  );

  // Need the plot's pixel bounds at drag-end so we can translate a
  // delta into a fraction of the plot. A ref + getBoundingClientRect
  // is enough; we don't need ResizeObserver because the drag handler
  // re-reads at the moment of the drop.
  const plotRef = useRef<HTMLDivElement | null>(null);

  // The DOM fires onClick after pointerup, by which point dnd-kit has
  // already set isDragging back to false. Without a guard the tile's
  // onClick opens the card modal at the end of every real drag. Set
  // this ref in onDragEnd whenever the user actually moved, then clear
  // it on the next frame -- the click event fires synchronously
  // before that, so the guard catches the spurious open.
  const justDraggedRef = useRef(false);

  const onDragEnd = async (e: DragEndEvent): Promise<void> => {
    const id = String(e.active.id);
    const row = plotted.find((r) => r.card.id === id);
    if (!row) return;

    const moved = e.delta.x !== 0 || e.delta.y !== 0;
    if (moved) {
      // Suppress the onClick that DOM will fire after pointerup.
      // requestAnimationFrame clears the flag AFTER the click event
      // has resolved on the next frame.
      justDraggedRef.current = true;
      requestAnimationFrame(() => {
        justDraggedRef.current = false;
      });
    }

    const plot = plotRef.current;
    if (!plot) return;
    const rect = plot.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    if (!yEditable || !moved) {
      // Drag was either inert (Y axis not editable) or a no-move
      // jiggle. Either way, nothing to write. The toolbar already
      // shows the "drag = no-op (v1)" hint for non-editable Y, so we
      // don't need a transient error toast on every drop.
      return;
    }

    // The coordinate math + snap + no-op detection all live in the pure
    // restakeFromDrag helper so they can be unit-tested without a DOM.
    // This handler just feeds it the drop geometry and acts on the result.
    const currentStakes =
      typeof row.card.frontmatter["stakes"] === "string"
        ? (row.card.frontmatter["stakes"] as string).toLowerCase()
        : null;
    const decision = restakeFromDrag({
      yNorm: row.yNorm,
      deltaYPx: e.delta.y,
      plotHeightPx: rect.height,
      currentStakes,
    });
    if (!decision) {
      // Nothing to write: same bucket, or a degenerate plot height.
      return;
    }
    const targetStakes = decision.targetStakes;

    // Snapshot the pre-drag card so a double-failure rollback can
    // restore it from the closure rather than punting to the server.
    const original = row.card;
    upsert({
      ...original,
      frontmatter: { ...original.frontmatter, stakes: targetStakes },
    });
    try {
      const updated = await api.patchCardFrontmatter(id, {
        stakes: targetStakes,
      });
      upsert(updated);
      setPatchError(null);
    } catch (err) {
      // Rollback: try the server first (most-authoritative), then
      // fall back to the pre-drag snapshot if even that fetch fails.
      // Strip `body` so we don't accidentally store CardDetail under
      // the CardSummary contract.
      try {
        const fresh = await api.getCard(id);
        const { body: _body, ...summary } = fresh;
        upsert(summary);
      } catch {
        upsert(original);
      }
      setPatchError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <div className="flex flex-col">
      <FilterBar />
      <div className="flex items-center gap-3 px-5 py-2 border-b border-border bg-panel/40">
        <AxisPicker
          label="X axis"
          value={xAxis}
          onChange={setXAxis}
          excluding={yAxis}
        />
        <AxisPicker
          label="Y axis"
          value={yAxis}
          onChange={setYAxis}
          excluding={xAxis}
        />
        <div className="ml-auto text-[11px] text-muted">
          <span className="mr-3">{plotted.length} plotted</span>
          {ungraphed.length > 0 ? (
            <span className="mr-3">{ungraphed.length} missing data</span>
          ) : null}
          <span>{yEditable ? "drag Y to restake" : "drag = no-op (v1)"}</span>
        </div>
      </div>
      <div className="px-5 py-4">
        {patchError ? (
          <div className="mb-3 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
            {patchError}
          </div>
        ) : null}
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          <Plot
            plotRef={plotRef}
            xAxis={xAxis}
            yAxis={yAxis}
            plotted={plotted}
            justDraggedRef={justDraggedRef}
            onOpenCard={(id) => setOpenCard(id)}
          />
        </DndContext>
        {ungraphed.length > 0 ? (
          <UngraphedTray
            xAxis={xAxis}
            yAxis={yAxis}
            rows={ungraphed}
            onOpenCard={(id) => setOpenCard(id)}
          />
        ) : null}
        <CardModal cardId={openCard} onClose={() => setOpenCard(null)} />
      </div>
    </div>
  );
}

function AxisPicker({
  label,
  value,
  onChange,
  excluding,
}: {
  label: string;
  value: AxisKey;
  onChange: (v: AxisKey) => void;
  excluding: AxisKey;
}) {
  return (
    <label className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted">
      <span>{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as AxisKey)}
        className="rounded border border-border bg-panel2 px-1.5 py-1 text-[12px] normal-case tracking-normal text-text focus:border-accent focus:outline-none"
      >
        {AXIS_OPTIONS.filter((o) => o.key !== excluding).map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
            {o.editable ? " (editable)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

interface PlottedRow {
  card: CardSummary;
  xRaw: number | null;
  yRaw: number | null;
  xNorm: number;
  yNorm: number;
}

function Plot({
  plotRef,
  xAxis,
  yAxis,
  plotted,
  justDraggedRef,
  onOpenCard,
}: {
  plotRef: React.MutableRefObject<HTMLDivElement | null>;
  xAxis: AxisKey;
  yAxis: AxisKey;
  plotted: PlottedRow[];
  justDraggedRef: React.MutableRefObject<boolean>;
  onOpenCard: (id: string) => void;
}) {
  const { setNodeRef: setDropRef } = useDroppable({ id: PLOT_ID });

  // Combined ref: dnd-kit's droppable ref + the page-level plotRef so
  // the drag handler can read the plot's bounding rect at drop time.
  const setRef = (el: HTMLDivElement | null): void => {
    plotRef.current = el;
    setDropRef(el);
  };

  return (
    <div
      ref={setRef}
      className="relative rounded border border-border bg-panel2/30 select-none"
      style={{ aspectRatio: "16 / 9", minHeight: 360 }}
      data-testid="grid-plot"
    >
      <QuadrantOverlay />
      <AxisLabels xAxis={xAxis} yAxis={yAxis} plotted={plotted} />
      {plotted.map((row) => (
        <PlottedTile
          key={row.card.id}
          row={row}
          justDraggedRef={justDraggedRef}
          onOpenCard={onOpenCard}
        />
      ))}
    </div>
  );
}

function PlottedTile({
  row,
  justDraggedRef,
  onOpenCard,
}: {
  row: PlottedRow;
  justDraggedRef: React.MutableRefObject<boolean>;
  onOpenCard: (id: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: row.card.id });

  // Position uses math-Y (0 = bottom, 1 = top), so flip for CSS.
  const leftPct = row.xNorm * 100;
  const topPct = (1 - row.yNorm) * 100;

  const color = projectColor(projectKeyOf(row.card));
  const quadrant = classifyQuadrant(row.xNorm, row.yNorm);

  const style: CSSProperties = {
    left: `${leftPct}%`,
    top: `${topPct}%`,
    transform: transform
      ? `translate3d(calc(-50% + ${transform.x}px), calc(-50% + ${transform.y}px), 0)`
      : "translate(-50%, -50%)",
    background: color,
    opacity: isDragging ? 0.6 : 0.95,
    zIndex: isDragging ? 30 : 10,
    cursor: isDragging ? "grabbing" : "grab",
  };

  return (
    <button
      ref={setNodeRef}
      style={style}
      className="absolute h-7 w-7 rounded-full border border-black/30 shadow-md hover:scale-110 transition-transform"
      onClick={(e) => {
        // Suppress the click that DOM fires after a real drag.
        // isDragging is already false by this point; justDraggedRef
        // is the reliable signal.
        e.preventDefault();
        if (isDragging || justDraggedRef.current) return;
        onOpenCard(row.card.id);
      }}
      title={`${cardTitle(row.card)} (${cardShortId(row.card)}) -- ${QUADRANT_LABEL[quadrant]}`}
      {...listeners}
      {...attributes}
      aria-label={`${cardTitle(row.card)} -- ${quadrant}`}
    />
  );
}

function QuadrantOverlay() {
  // 2x2 grid of muted quadrant labels rendered behind the points.
  // The labels match the cardinal classifyQuadrant() returns: top-left
  // = priority, top-right = do-carefully, bottom-left = backlog,
  // bottom-right = cancel.
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 grid grid-cols-2 grid-rows-2"
    >
      <QuadLabel pos="top-left" label="priority" />
      <QuadLabel pos="top-right" label="do carefully" />
      <QuadLabel pos="bottom-left" label="backlog" />
      <QuadLabel pos="bottom-right" label="cancel / downtier" />
      {/* Crosshair through the center. */}
      <div className="absolute top-0 bottom-0 left-1/2 w-px bg-border" />
      <div className="absolute left-0 right-0 top-1/2 h-px bg-border" />
    </div>
  );
}

function QuadLabel({
  pos,
  label,
}: {
  pos: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  label: string;
}) {
  const align = {
    "top-left": "items-start justify-start",
    "top-right": "items-start justify-end",
    "bottom-left": "items-end justify-start",
    "bottom-right": "items-end justify-end",
  }[pos];
  return (
    <div className={`flex p-2 ${align}`}>
      <span className="text-[10px] uppercase tracking-widest text-muted/60">
        {label}
      </span>
    </div>
  );
}

function AxisLabels({
  xAxis,
  yAxis,
  plotted,
}: {
  xAxis: AxisKey;
  yAxis: AxisKey;
  plotted: PlottedRow[];
}) {
  const xMax = plotted.reduce(
    (m, r) => (r.xRaw !== null && r.xRaw > m ? r.xRaw : m),
    0
  );
  const yMax = plotted.reduce(
    (m, r) => (r.yRaw !== null && r.yRaw > m ? r.yRaw : m),
    0
  );
  return (
    <>
      <div className="pointer-events-none absolute -bottom-5 left-0 right-0 flex justify-between text-[10px] text-muted">
        <span>{axisLowLabel(xAxis)}</span>
        <span className="font-mono">
          {labelFor("x-max", xAxis, xMax)} {axisName(xAxis)} →
        </span>
      </div>
      <div className="pointer-events-none absolute top-0 bottom-0 -left-3 flex flex-col justify-between text-[10px] text-muted">
        <span className="font-mono whitespace-nowrap">
          ↑ {labelFor("y-max", yAxis, yMax)} {axisName(yAxis)}
        </span>
        <span>{axisLowLabel(yAxis)}</span>
      </div>
    </>
  );
}

function axisName(a: AxisKey): string {
  return AXIS_OPTIONS.find((o) => o.key === a)?.label ?? a;
}

function axisLowLabel(a: AxisKey): string {
  if (a === "stakes") return STAKES_ORDER[0]!;
  if (a === "cost") return "$0";
  return "low";
}

function labelFor(_pos: string, a: AxisKey, max: number): string {
  if (a === "stakes") return STAKES_ORDER[STAKES_ORDER.length - 1]!;
  if (a === "cost") return formatCost(max);
  return Number.isFinite(max) ? String(max) : "max";
}

function UngraphedTray({
  xAxis,
  yAxis,
  rows,
  onOpenCard,
}: {
  xAxis: AxisKey;
  yAxis: AxisKey;
  rows: Array<{ card: CardSummary; xRaw: number | null; yRaw: number | null }>;
  onOpenCard: (id: string) => void;
}) {
  return (
    <div className="mt-6 rounded border border-dashed border-border bg-panel2/30 px-3 py-2">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">
        Missing axis data ({rows.length})
      </div>
      <div className="flex flex-wrap gap-1">
        {rows.map((r) => {
          const color = projectColor(projectKeyOf(r.card));
          const missing: string[] = [];
          if (r.xRaw === null) missing.push(axisName(xAxis));
          if (r.yRaw === null) missing.push(axisName(yAxis));
          return (
            <button
              key={r.card.id}
              onClick={() => onOpenCard(r.card.id)}
              className="flex items-center gap-1.5 rounded border border-border bg-panel px-2 py-1 text-[11px] text-text hover:border-accent"
              title={`Missing: ${missing.join(", ")}`}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: color }}
              />
              <span className="font-mono">{cardShortId(r.card)}</span>
              <span className="truncate max-w-[200px]">
                {cardTitle(r.card)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
