import { NavLink } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import { ViewMenu } from "./ViewMenu";

interface Props {
  onRefresh: () => void;
  onOpenPalette?: () => void;
  cardsDir?: string | undefined;
}

const NAV: Array<{ to: string; label: string }> = [
  { to: "/", label: "Kanban" },
  { to: "/grid", label: "Grid" },
  { to: "/submit", label: "Submit Story" },
  { to: "/sprints", label: "Sprint Planner" },
  { to: "/retros", label: "Retros" },
];

export function Header({ onRefresh, onOpenPalette, cardsDir }: Props) {
  const { signOut } = useAuth();

  return (
    <header className="flex items-center gap-4 px-5 py-3 bg-panel border-b border-border">
      <h1 className="text-[15px] font-semibold tracking-tight flex items-center gap-2">
        <span className="inline-block w-2 h-2 rounded-sm bg-accent" />
        agile-cards
      </h1>
      <span className="text-[11px] uppercase tracking-wider text-muted border border-border px-1.5 py-0.5 rounded">
        board v0+
      </span>
      <nav className="flex items-center gap-1 ml-2">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={({ isActive }) =>
              [
                "px-2.5 py-1 text-xs rounded transition-colors",
                isActive
                  ? "bg-panel2 text-text border border-border"
                  : "text-muted hover:text-text hover:bg-panel2",
              ].join(" ")
            }
            end={n.to === "/"}
          >
            {n.label}
          </NavLink>
        ))}
      </nav>
      <div className="flex-1" />
      {cardsDir ? (
        <span
          className="text-xs text-muted max-w-[360px] overflow-hidden whitespace-nowrap text-ellipsis"
          title={cardsDir}
        >
          {cardsDir}
        </span>
      ) : null}
      {onOpenPalette ? (
        <button
          className="btn"
          onClick={onOpenPalette}
          title="open command palette (Cmd/Ctrl-K)"
        >
          <span className="mr-1">Search</span>
          <kbd className="rounded border border-border bg-panel px-1 text-[10px] text-muted">
            ⌘K
          </kbd>
        </button>
      ) : null}
      <ViewMenu />
      <button className="btn" onClick={onRefresh}>
        Refresh
      </button>
      <button className="btn" onClick={signOut}>
        Sign out
      </button>
    </header>
  );
}
