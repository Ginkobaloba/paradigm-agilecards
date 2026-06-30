import { useEffect, useRef, useState } from "react";

/**
 * A single dismissable filter chip. Renders as a small pill that opens
 * a popover with its values when clicked. Selection is multi-value
 * (union within this chip). When the popover closes the parent
 * decides what to render; this component just owns the open/close
 * state and the visible affordance.
 *
 * Generic over the value type so a tier chip can use `number` and a
 * project chip can use `string` without a wrapper.
 */
interface Props<T extends string | number> {
  label: string;
  selected: readonly T[];
  options: readonly T[];
  onToggle: (value: T) => void;
  onClear: () => void;
  /** Convert a value to its display label. Defaults to String(value). */
  format?: (v: T) => string;
}

export function FilterChip<T extends string | number>({
  label,
  selected,
  options,
  onToggle,
  onClear,
  format = (v) => String(v),
}: Props<T>) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Close when clicking outside the chip + popover.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = selected.length > 0;

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={[
          "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
          active
            ? "border-accent/40 bg-accent/10 text-text"
            : "border-border bg-panel2 text-muted hover:text-text",
        ].join(" ")}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{label}</span>
        {active ? (
          <span className="rounded-full bg-accent/30 px-1 text-[10px] font-medium text-text">
            {selected.length}
          </span>
        ) : null}
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="opacity-60"
          aria-hidden
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open ? (
        <div
          className="absolute z-30 mt-1 min-w-[160px] surface flex flex-col py-1 shadow-xl shadow-black/40"
          role="listbox"
        >
          {options.length === 0 ? (
            <div className="px-2 py-1 text-[11px] text-muted italic">
              no values
            </div>
          ) : (
            options.map((v) => {
              const isOn = selected.includes(v);
              return (
                <button
                  key={String(v)}
                  type="button"
                  onClick={() => onToggle(v)}
                  className={[
                    "flex items-center gap-2 px-2 py-1 text-left text-[12px] transition-colors",
                    isOn
                      ? "text-text bg-accent/10"
                      : "text-muted hover:text-text hover:bg-panel2",
                  ].join(" ")}
                  role="option"
                  aria-selected={isOn}
                >
                  <span
                    className={[
                      "inline-block h-3 w-3 shrink-0 rounded border",
                      isOn
                        ? "border-accent bg-accent"
                        : "border-border bg-transparent",
                    ].join(" ")}
                  />
                  <span className="font-mono">{format(v)}</span>
                </button>
              );
            })
          )}
          {active ? (
            <button
              type="button"
              onClick={() => {
                onClear();
                setOpen(false);
              }}
              className="mt-1 border-t border-border px-2 py-1 text-left text-[11px] text-muted hover:text-text"
            >
              Clear {label.toLowerCase()}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * A tri-state chip: Any / Yes / No. Used for pin_required and
 * extended_thinking, where the field is a boolean and the user wants
 * "show only true", "show only false", or "do not filter".
 */
export function TriChip({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (v: boolean | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const active = value !== null;
  const displayValue =
    value === true ? "yes" : value === false ? "no" : "any";

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={[
          "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] transition-colors",
          active
            ? "border-accent/40 bg-accent/10 text-text"
            : "border-border bg-panel2 text-muted hover:text-text",
        ].join(" ")}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{label}</span>
        <span className="text-[10px] font-medium uppercase tracking-wide opacity-70">
          {displayValue}
        </span>
      </button>
      {open ? (
        <div
          className="absolute z-30 mt-1 min-w-[100px] surface flex flex-col py-1 shadow-xl shadow-black/40"
          role="listbox"
        >
          {([
            ["any", null],
            ["yes", true],
            ["no", false],
          ] as const).map(([labelText, val]) => {
            const isOn = value === val;
            return (
              <button
                key={labelText}
                type="button"
                onClick={() => {
                  onChange(val);
                  setOpen(false);
                }}
                className={[
                  "px-2 py-1 text-left text-[12px] transition-colors",
                  isOn
                    ? "text-text bg-accent/10"
                    : "text-muted hover:text-text hover:bg-panel2",
                ].join(" ")}
              >
                {labelText}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
