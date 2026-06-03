import { useCallback, useEffect, useState } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Cheatsheet } from "./components/Cheatsheet";
import { CommandPalette } from "./components/CommandPalette";
import { CardModal } from "./components/CardModal";
import { Header } from "./components/Header";
import { TokenGate } from "./components/TokenGate";
import { useAuth } from "./hooks/useAuth";
import { useCards } from "./hooks/useCards";
import { useKeyboard } from "./hooks/useKeyboard";
import { useRates } from "./hooks/useRates";
import { useSSE } from "./hooks/useSSE";
import { api } from "./lib/api";
import { filtersFromParams, filtersToParams, useFilters } from "./state/filters";
import { Grid } from "./routes/Grid";
import { Kanban } from "./routes/Kanban";
import { Retros } from "./routes/Retros";
import { SprintDetail } from "./routes/SprintDetail";
import { SprintPlanner } from "./routes/SprintPlanner";
import { SubmitStory } from "./routes/SubmitStory";

/**
 * Root component. Gate on auth, then mount the dashboard. Health info
 * (e.g. CARDS_DIR) flows through the header so the user sees which tree
 * they're looking at.
 *
 * App also owns three pieces of cross-page state:
 *   - the command palette open/close (Cmd-K et al.)
 *   - the cheatsheet open/close (?)
 *   - the "open card via palette" handoff, which mounts a CardModal
 *     here rather than inside Kanban so the palette works from any
 *     route.
 *
 * Filter state in `useFilters` syncs to the URL query string on every
 * change; on initial load (or browser back/forward), the URL is
 * authoritative.
 */
export function App() {
  const { isAuthed, signIn } = useAuth();
  const { loading, error, refresh } = useCards(isAuthed);
  const rates = useRates(isAuthed);
  const [cardsDir, setCardsDir] = useState<string | undefined>(undefined);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [cheatOpen, setCheatOpen] = useState(false);
  const [paletteCard, setPaletteCard] = useState<string | null>(null);

  useSSE(isAuthed);
  useFilterUrlSync(isAuthed);

  const focusFilterSearch = useCallback((): void => {
    const el = document.getElementById("filter-search");
    if (el instanceof HTMLInputElement) {
      el.focus();
      el.select();
    } else {
      // No filter bar on this route -> fall back to opening palette.
      setPaletteOpen(true);
    }
  }, []);

  useKeyboard({
    openPalette: () => setPaletteOpen(true),
    toggleCheatsheet: () => setCheatOpen((o) => !o),
    focusFilterSearch,
  });

  useEffect(() => {
    if (!isAuthed) return;
    void api
      .health()
      .then((h) => setCardsDir(h.cardsDir))
      .catch(() => {
        /* ignore; header just hides the path */
      });
  }, [isAuthed]);

  if (!isAuthed) {
    return <TokenGate onAuthed={signIn} />;
  }

  return (
    <div className="min-h-screen flex flex-col">
      <Header
        onRefresh={() => void refresh()}
        onOpenPalette={() => setPaletteOpen(true)}
        cardsDir={cardsDir}
      />
      <main className="flex-1">
        <Routes>
          <Route
            path="/"
            element={<Kanban loading={loading} error={error} rates={rates} />}
          />
          <Route path="/submit" element={<SubmitStory />} />
          <Route path="/grid" element={<Grid rates={rates} />} />
          <Route path="/sprints" element={<SprintPlanner />} />
          <Route path="/sprints/:id" element={<SprintDetail />} />
          <Route path="/retros" element={<Retros />} />
        </Routes>
      </main>
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onOpenCard={(id) => setPaletteCard(id)}
        onRefresh={() => void refresh()}
      />
      <Cheatsheet open={cheatOpen} onClose={() => setCheatOpen(false)} />
      <CardModal
        cardId={paletteCard}
        onClose={() => setPaletteCard(null)}
      />
    </div>
  );
}

/**
 * Keep the filter state and the URL query string in sync. The URL is
 * authoritative on initial load and on browser back/forward; the
 * filter store is authoritative on user interaction.
 */
function useFilterUrlSync(isAuthed: boolean): void {
  const location = useLocation();
  const navigate = useNavigate();
  const setAll = useFilters((s) => s.setAll);

  // Pull filters out of the URL on mount and whenever the path/query
  // changes externally (back button). Skip while not authed -- the
  // gate page shouldn't fight the URL.
  useEffect(() => {
    if (!isAuthed) return;
    if (location.pathname !== "/") return;
    const params = new URLSearchParams(location.search);
    setAll(filtersFromParams(params));
    // We DO want this to re-run when the URL changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname, location.search, isAuthed]);

  // Push filter changes back into the URL. Subscribed via store API to
  // avoid a re-render of App on every keystroke.
  useEffect(() => {
    if (!isAuthed) return;
    const unsub = useFilters.subscribe((state) => {
      if (window.location.pathname !== "/") return;
      const next = filtersToParams(state).toString();
      const current = window.location.search.replace(/^\?/, "");
      if (next === current) return;
      navigate({ pathname: "/", search: next ? `?${next}` : "" }, { replace: true });
    });
    return unsub;
  }, [isAuthed, navigate]);
}
