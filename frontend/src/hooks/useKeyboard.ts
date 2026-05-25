/**
 * Global keyboard shortcuts. Mounted once at the App level so a single
 * handler owns all the cross-page bindings. Per-component bindings
 * (modal Esc, dnd-kit, Radix Dialog) keep using their own listeners.
 *
 * Shortcuts shipped here:
 *   Cmd/Ctrl-K   open command palette
 *   /            open command palette (only when not typing in an
 *                editable field)
 *   F            focus the filter-bar search input
 *   ?            open the cheatsheet
 *   Esc          handled per-component (palette/modal/popover)
 *
 * "Focused card" gestures (S to change status, X to multi-select) are
 * deferred until the kanban has a focused-card concept in its store
 * and a corresponding visible focus style on the tile.
 */

import { useEffect } from "react";

export interface KeyboardActions {
  openPalette: () => void;
  toggleCheatsheet: () => void;
  focusFilterSearch: () => void;
}

/**
 * True if `el` is an editable surface where alphanumeric shortcuts
 * should NOT fire. Includes inputs, textareas, contenteditable, and
 * the cmd-k palette itself.
 */
function isEditable(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  if (el.getAttribute("role") === "textbox") return true;
  return false;
}

export function useKeyboard(actions: KeyboardActions): void {
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      // Cmd-K / Ctrl-K: open palette. Stays bound even when typing,
      // because the modifier disambiguates.
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        actions.openPalette();
        return;
      }
      // Bare-key shortcuts only fire outside editable surfaces.
      if (isEditable(e.target)) return;

      if (e.key === "/") {
        e.preventDefault();
        actions.openPalette();
        return;
      }
      if (e.key === "?") {
        e.preventDefault();
        actions.toggleCheatsheet();
        return;
      }
      // Lowercase-only checks for letter shortcuts; bail when a
      // modifier is held so we don't fight browser shortcuts.
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        actions.focusFilterSearch();
        return;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [actions]);
}
