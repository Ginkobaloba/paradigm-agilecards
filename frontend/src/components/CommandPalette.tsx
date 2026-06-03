import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../hooks/useAuth";
import type { CardSummary } from "../lib/api";
import { fuzzyRank } from "../lib/fuzzy";
import { cardTitle } from "../lib/parseCard";
import { useStore } from "../state/store";

interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Opens the card detail modal at `cardId`. */
  onOpenCard: (cardId: string) => void;
  /** Triggers a manual refresh of the card list. */
  onRefresh: () => void;
}

const RECENT_KEY = "agile-cards.cmdk.recent";
const RECENT_MAX = 6;

function readRecent(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed.filter((x) => typeof x === "string");
    return [];
  } catch {
    return [];
  }
}

function writeRecent(ids: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      RECENT_KEY,
      JSON.stringify(ids.slice(0, RECENT_MAX))
    );
  } catch {
    /* ignore quota */
  }
}

/**
 * The Cmd-K palette. Opens on Cmd/Ctrl-K or `/`; type to fuzzy-search
 * across cards and commands. Recent cards float to the top when the
 * query is empty.
 *
 * Built on Radix Dialog so focus management and Esc handling come for
 * free. The list keyboard navigation (Up/Down/Enter) is wired by
 * hand because we want it to skip non-actionable rows (e.g. headers).
 */
export function CommandPalette({ open, onClose, onOpenCard, onRefresh }: Props) {
  const cards = useStore((s) => s.cards);
  const navigate = useNavigate();
  const { signOut } = useAuth();

  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const [recent, setRecent] = useState<string[]>(readRecent);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Reset query and focus the input every time we open.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIdx(0);
    setRecent(readRecent());
    // Defer focus until the dialog has mounted.
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const commands: Command[] = useMemo(
    () => [
      {
        id: "cmd:submit",
        label: "Go to Submit Story",
        hint: "/submit",
        run: () => navigate("/submit"),
      },
      {
        id: "cmd:kanban",
        label: "Go to Kanban",
        hint: "/",
        run: () => navigate("/"),
      },
      {
        id: "cmd:grid",
        label: "Go to Grid (spend-side optimizer)",
        hint: "/grid",
        run: () => navigate("/grid"),
      },
      {
        id: "cmd:sprints",
        label: "Go to Sprint Planner",
        hint: "/sprints",
        run: () => navigate("/sprints"),
      },
      {
        id: "cmd:retros",
        label: "Go to Retros",
        hint: "/retros",
        run: () => navigate("/retros"),
      },
      {
        id: "cmd:refresh",
        label: "Refresh the board",
        hint: "/api/cards",
        run: () => onRefresh(),
      },
      {
        id: "cmd:signout",
        label: "Sign out",
        run: () => signOut(),
      },
    ],
    [navigate, onRefresh, signOut]
  );

  const cardList = useMemo(() => Object.values(cards), [cards]);

  const cardMatches = useMemo(() => {
    if (query.length === 0) {
      // No query -> recents on top, then a slice of cards by id for
      // discoverability.
      const recentCards = recent
        .map((id) => cardList.find((c) => c.id === id))
        .filter((c): c is CardSummary => Boolean(c));
      const rest = cardList
        .filter((c) => !recent.includes(c.id))
        .slice(0, 24);
      return [...recentCards, ...rest].slice(0, 30);
    }
    return fuzzyRank(
      query,
      cardList,
      (c) => `${cardTitle(c)} ${c.id}`,
      30
    ).map((r) => r.item);
  }, [query, cardList, recent]);

  const commandMatches = useMemo(() => {
    if (query.length === 0) return commands;
    return fuzzyRank(query, commands, (c) => c.label, 10).map((r) => r.item);
  }, [query, commands]);

  // Flatten cards + commands into a single index for arrow navigation.
  const rows = useMemo(() => {
    return [
      ...cardMatches.map((c) => ({ kind: "card" as const, card: c })),
      ...commandMatches.map((c) => ({ kind: "cmd" as const, command: c })),
    ];
  }, [cardMatches, commandMatches]);

  useEffect(() => {
    // Whenever the row set changes, clamp activeIdx.
    if (activeIdx >= rows.length) setActiveIdx(0);
  }, [rows.length, activeIdx]);

  const runRow = (idx: number): void => {
    const row = rows[idx];
    if (!row) return;
    if (row.kind === "card") {
      const next = [
        row.card.id,
        ...recent.filter((id) => id !== row.card.id),
      ].slice(0, RECENT_MAX);
      writeRecent(next);
      setRecent(next);
      onOpenCard(row.card.id);
      onClose();
    } else {
      row.command.run();
      onClose();
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(rows.length - 1, i + 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(0, i - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      runRow(activeIdx);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40" />
        <Dialog.Content
          className="fixed top-[12vh] left-1/2 -translate-x-1/2 w-[min(640px,92vw)] max-h-[70vh] z-50 surface flex flex-col overflow-hidden"
          onOpenAutoFocus={(e) => e.preventDefault()}
          aria-label="Command palette"
          onKeyDown={onKey}
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <span className="text-[11px] uppercase tracking-wider text-muted">
              Cmd-K
            </span>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActiveIdx(0);
              }}
              placeholder="Search cards, run commands..."
              className="flex-1 bg-transparent text-sm text-text placeholder:text-muted focus:outline-none"
              aria-label="search cards and commands"
            />
          </div>
          <div
            ref={listRef}
            className="flex-1 overflow-y-auto p-1"
            role="listbox"
          >
            {cardMatches.length > 0 ? (
              <div className="mb-1">
                <Header>
                  {query.length === 0 && recent.length > 0
                    ? "Recent / Cards"
                    : "Cards"}
                </Header>
                {cardMatches.map((c, i) => {
                  const idx = i;
                  return (
                    <Row
                      key={c.id}
                      active={activeIdx === idx}
                      onMouseEnter={() => setActiveIdx(idx)}
                      onClick={() => runRow(idx)}
                      primary={cardTitle(c)}
                      secondary={c.id}
                    />
                  );
                })}
              </div>
            ) : null}
            {commandMatches.length > 0 ? (
              <div>
                <Header>Commands</Header>
                {commandMatches.map((cmd, i) => {
                  const idx = cardMatches.length + i;
                  return (
                    <Row
                      key={cmd.id}
                      active={activeIdx === idx}
                      onMouseEnter={() => setActiveIdx(idx)}
                      onClick={() => runRow(idx)}
                      primary={cmd.label}
                      secondary={cmd.hint ?? ""}
                    />
                  );
                })}
              </div>
            ) : null}
            {rows.length === 0 ? (
              <div className="px-3 py-6 text-center text-sm text-muted italic">
                no matches
              </div>
            ) : null}
          </div>
          <div className="border-t border-border px-3 py-1.5 text-[10px] text-muted flex justify-between">
            <span>↑↓ navigate · Enter open · Esc close</span>
            <span className="font-mono">
              {rows.length} match{rows.length === 1 ? "" : "es"}
            </span>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Header({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-muted">
      {children}
    </div>
  );
}

function Row({
  active,
  onMouseEnter,
  onClick,
  primary,
  secondary,
}: {
  active: boolean;
  onMouseEnter: () => void;
  onClick: () => void;
  primary: string;
  secondary: string;
}) {
  return (
    <button
      type="button"
      onMouseEnter={onMouseEnter}
      onClick={onClick}
      className={[
        "flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors",
        active ? "bg-accent/10 text-text" : "text-muted hover:bg-panel2",
      ].join(" ")}
      role="option"
      aria-selected={active}
    >
      <span className="truncate">{primary}</span>
      <span className="font-mono text-[11px] text-muted shrink-0">{secondary}</span>
    </button>
  );
}
