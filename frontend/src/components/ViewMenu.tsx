import { useEffect, useRef, useState } from "react";

import { api, ApiError, type SavedView } from "../lib/api";
import {
  EMPTY_FILTERS,
  type FilterState,
  filtersToParams,
  useFilters,
} from "../state/filters";

/**
 * Header-level menu for saved views. Lets the user:
 *   - list views saved against the current token
 *   - load a view (overwrites current filters)
 *   - save the current filter state as a new view
 *   - update the currently-loaded view's payload
 *   - delete a view
 *   - copy a share-URL that encodes the current filter state in the
 *     query string
 *
 * Views persist server-side keyed by token id. Sharing a view across
 * tokens happens by URL; the server never lets one token read another's
 * saved-view rows.
 */
export function ViewMenu() {
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState<SavedView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [copied, setCopied] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const filters = useFilters();
  const setAll = useFilters((s) => s.setAll);

  // Lazy-load views the first time the dropdown opens.
  useEffect(() => {
    if (!open || views.length > 0) return;
    let cancelled = false;
    void api
      .listViews()
      .then((r) => {
        if (!cancelled) setViews(r.views);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, views.length]);

  // Click outside to close.
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

  const currentView = views.find((v) => v.id === currentId);

  const handleLoad = (view: SavedView): void => {
    const payload = view.payload as Partial<FilterState> | null;
    setAll({ ...EMPTY_FILTERS, ...(payload ?? {}) });
    setCurrentId(view.id);
    setOpen(false);
  };

  const handleSaveNew = async (): Promise<void> => {
    const name = newName.trim();
    if (name.length === 0) return;
    try {
      const view = await api.createView(name, filters);
      setViews((vs) => [...vs, view].sort((a, b) => a.name.localeCompare(b.name)));
      setCurrentId(view.id);
      setNewName("");
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const handleUpdateCurrent = async (): Promise<void> => {
    if (currentId === null) return;
    try {
      const updated = await api.updateView(currentId, { payload: filters });
      setViews((vs) => vs.map((v) => (v.id === updated.id ? updated : v)));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const handleDelete = async (view: SavedView): Promise<void> => {
    if (!window.confirm(`Delete view "${view.name}"?`)) return;
    try {
      await api.deleteView(view.id);
      setViews((vs) => vs.filter((v) => v.id !== view.id));
      if (currentId === view.id) setCurrentId(null);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const handleCopyLink = async (): Promise<void> => {
    if (typeof window === "undefined") return;
    const params = filtersToParams(filters);
    const url = `${window.location.origin}${window.location.pathname}?${params.toString()}`;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard denied -> tooltip already shows the action attempted */
    }
  };

  const label = currentView ? currentView.name : "View";

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="btn"
        aria-haspopup="menu"
        aria-expanded={open}
        title="saved views"
      >
        <span className="text-[10px] uppercase tracking-wide text-muted mr-1">
          view:
        </span>
        <span className="text-text">{label}</span>
      </button>
      {open ? (
        <div className="absolute right-0 z-40 mt-1 w-[280px] surface flex flex-col py-1 shadow-xl shadow-black/40">
          {error ? (
            <div className="mx-2 my-1 rounded border border-danger/30 bg-danger/10 px-2 py-1 text-[11px] text-danger">
              {error}
            </div>
          ) : null}
          <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-muted">
            Saved views
          </div>
          {views.length === 0 ? (
            <div className="px-2 py-1 text-[11px] italic text-muted">
              none yet
            </div>
          ) : (
            views.map((v) => (
              <div
                key={v.id}
                className={[
                  "group flex items-center gap-1 px-2 py-1 text-[12px]",
                  currentId === v.id ? "bg-accent/10" : "hover:bg-panel2",
                ].join(" ")}
              >
                <button
                  type="button"
                  onClick={() => handleLoad(v)}
                  className="flex-1 text-left text-text truncate"
                >
                  {v.name}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDelete(v)}
                  className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger text-[14px] leading-none px-1"
                  aria-label={`delete view ${v.name}`}
                  title={`delete view ${v.name}`}
                >
                  ×
                </button>
              </div>
            ))
          )}

          <div className="my-1 border-t border-border" />
          <div className="px-2 py-1 flex items-center gap-1">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Save as..."
              className="flex-1 rounded border border-border bg-panel2 px-1.5 py-0.5 text-[12px] text-text placeholder:text-muted focus:border-accent focus:outline-none"
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSaveNew();
              }}
            />
            <button
              type="button"
              onClick={() => void handleSaveNew()}
              className="rounded border border-border bg-panel2 px-2 py-0.5 text-[11px] text-muted hover:text-text"
              disabled={newName.trim().length === 0}
            >
              Save
            </button>
          </div>
          {currentView ? (
            <button
              type="button"
              onClick={() => void handleUpdateCurrent()}
              className="px-2 py-1 text-left text-[12px] text-muted hover:text-text hover:bg-panel2"
            >
              Update "{currentView.name}" to current filters
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void handleCopyLink()}
            className="px-2 py-1 text-left text-[12px] text-muted hover:text-text hover:bg-panel2"
          >
            {copied ? "Copied link to clipboard" : "Copy share link"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
