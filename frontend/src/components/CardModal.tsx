import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api, ApiError, type CardDetail } from "../lib/api";

interface Props {
  cardId: string | null;
  onClose: () => void;
}

/**
 * Full-card view. Loads the body lazily when opened; the kanban store
 * only holds the frontmatter summary.
 */
export function CardModal({ cardId, onClose }: Props) {
  const [card, setCard] = useState<CardDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCard(null);
    setError(null);
    if (!cardId) return;
    void api
      .getCard(cardId)
      .then((c) => {
        if (!cancelled) setCard(c);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [cardId]);

  return (
    <Dialog.Root open={cardId !== null} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40" />
        <Dialog.Content
          className="fixed top-[5vh] left-1/2 -translate-x-1/2 w-[min(900px,92vw)] max-h-[90vh] z-50 surface flex flex-col"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <Dialog.Title className="text-sm font-semibold text-text">
              {card
                ? typeof card.frontmatter["title"] === "string"
                  ? (card.frontmatter["title"] as string)
                  : card.id
                : cardId ?? ""}
            </Dialog.Title>
            <Dialog.Close className="btn" aria-label="Close">
              Close
            </Dialog.Close>
          </div>
          <div className="overflow-y-auto p-4 flex flex-col gap-4">
            {error ? (
              <div className="text-danger text-sm">{error}</div>
            ) : !card ? (
              <div className="text-muted text-sm italic">loading…</div>
            ) : (
              <>
                <FrontmatterTable fm={card.frontmatter} file={card.file} />
                <div className="markdown text-sm">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {card.body}
                  </ReactMarkdown>
                </div>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function FrontmatterTable({
  fm,
  file,
}: {
  fm: Record<string, unknown>;
  file: string;
}) {
  const entries = Object.entries(fm);
  return (
    <div className="surface-2 p-3 text-xs font-mono">
      <Row k="file" v={file} />
      {entries.map(([k, v]) => (
        <Row key={k} k={k} v={v} />
      ))}
    </div>
  );
}

function Row({ k, v }: { k: string; v: unknown }) {
  // Dim empty values (null / undefined / []) so the eye skips straight
  // to the fields that actually carry information.
  const isEmpty =
    v === null || v === undefined || (Array.isArray(v) && v.length === 0);
  return (
    <div className="flex gap-2 py-0.5 hover:bg-panel/60">
      <span className="text-accent shrink-0 w-40">{k}</span>
      <span
        className={[
          "whitespace-pre-wrap break-words",
          isEmpty ? "text-muted/50" : "text-text",
        ].join(" ")}
      >
        {renderValue(v)}
      </span>
    </div>
  );
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
