/**
 * Triage inbox (roadmap 2.4): the pre-backlog lane.
 *
 * Staged submit-story batches surface here per-card: title, body
 * excerpt, tier, $ estimate, and a "Similar to" section that flags
 * near-duplicate existing cards by title similarity (token Jaccard,
 * `lib/similarity.ts`). Three one-click resolutions per card:
 *
 *   - Promote: into `backlog/` (the SSE stream announces it; the
 *     kanban picks it up without a refresh here).
 *   - Merge into a flagged similar card: the staged body is absorbed
 *     as an "Absorbed from triage" section on the target.
 *   - Decline: parked server-side under `_declined/`, recoverable.
 *
 * The list is fetch-on-mount + refetch-after-action rather than
 * SSE-driven: staged files live outside the watched status folders,
 * so the live card stream does not announce them. Fine for v1; an
 * inbox is a deliberate-review surface, not a live wallboard.
 */

import { useCallback, useEffect, useState } from "react";

import { triageApi, type TriageBatch, type TriageCard } from "../lib/api";
import { costForTokens, formatCost, type RatesPayload } from "../lib/cost";
import { cardShortId, cardTitle } from "../lib/parseCard";
import { rankSimilar } from "../lib/similarity";
import { useStore } from "../state/store";

interface Props {
  rates: RatesPayload;
}

export function Triage({ rates }: Props) {
  const [batches, setBatches] = useState<TriageBatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // "batchId/file"
  const cards = useStore((s) => s.cards);

  const load = useCallback(async (): Promise<void> => {
    try {
      const { batches } = await triageApi.list();
      setBatches(batches);
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = useCallback(
    async (key: string, action: () => Promise<unknown>): Promise<void> => {
      setBusy(key);
      try {
        await action();
        await load();
      } catch (err) {
        setError(String(err));
      } finally {
        setBusy(null);
      }
    },
    [load]
  );

  const existing = Object.values(cards);
  const totalCards =
    batches?.reduce((n, b) => n + b.cards.length, 0) ?? 0;

  return (
    <div className="px-5 py-4 max-w-4xl">
      <div className="flex items-baseline gap-3 mb-1">
        <h2 className="text-lg font-semibold">Triage inbox</h2>
        {batches !== null ? (
          <span className="text-xs text-muted">
            {totalCards === 0
              ? "empty"
              : `${totalCards} card${totalCards === 1 ? "" : "s"} awaiting review`}
          </span>
        ) : null}
      </div>
      <p className="text-xs text-muted mb-4">
        Staged submit-story output, resolved per card. Promote feeds the
        backlog; merge absorbs a near-duplicate into the existing card;
        decline parks the file recoverably.
      </p>

      {error ? (
        <div className="text-sm text-red-400 border border-red-900/50 rounded px-3 py-2 mb-4">
          {error}
        </div>
      ) : null}

      {batches === null ? (
        <div className="text-sm text-muted">Loading...</div>
      ) : batches.length === 0 ? (
        <div className="text-sm text-muted border border-dashed border-border rounded px-4 py-6">
          Nothing to triage. New submit-story batches land here.
        </div>
      ) : (
        batches.map((batch) => (
          <section key={batch.batchId} className="mb-6">
            <h3 className="text-sm font-medium mb-2 flex items-baseline gap-2">
              <span>{batch.batchId}</span>
              {batch.story ? (
                <span
                  className="text-xs text-muted font-normal max-w-[480px] overflow-hidden whitespace-nowrap text-ellipsis"
                  title={batch.story}
                >
                  {batch.story}
                </span>
              ) : null}
            </h3>
            <ul className="space-y-2">
              {batch.cards.map((card) => (
                <TriageRow
                  key={card.file}
                  batchId={batch.batchId}
                  card={card}
                  rates={rates}
                  existing={existing}
                  busy={busy === `${batch.batchId}/${card.file}`}
                  anyBusy={busy !== null}
                  onPromote={() =>
                    act(`${batch.batchId}/${card.file}`, () =>
                      triageApi.promote(batch.batchId, card.file)
                    )
                  }
                  onDecline={() =>
                    act(`${batch.batchId}/${card.file}`, () =>
                      triageApi.decline(batch.batchId, card.file)
                    )
                  }
                  onMerge={(targetId) =>
                    act(`${batch.batchId}/${card.file}`, () =>
                      triageApi.merge(batch.batchId, card.file, targetId)
                    )
                  }
                />
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}

interface RowProps {
  batchId: string;
  card: TriageCard;
  rates: RatesPayload;
  existing: ReadonlyArray<
    import("../lib/api").CardSummary
  >;
  busy: boolean;
  anyBusy: boolean;
  onPromote: () => void;
  onDecline: () => void;
  onMerge: (targetId: string) => void;
}

function TriageRow({
  card,
  rates,
  existing,
  busy,
  anyBusy,
  onPromote,
  onDecline,
  onMerge,
}: RowProps) {
  const est =
    card.estimatedTokens !== null
      ? costForTokens(
          card.estimatedTokens,
          card.model,
          rates.rates,
          rates.defaultInputRatio
        )
      : null;
  const similar = rankSimilar(card.title, existing, cardTitle);

  return (
    <li className="border border-border rounded bg-panel px-3 py-2.5">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-sm font-medium">{card.title}</span>
            <span className="text-[11px] text-muted font-mono">
              {card.id}
            </span>
            {card.tier !== null ? (
              <span className="text-[10px] uppercase tracking-wider text-muted border border-border px-1 py-0.5 rounded">
                tier {card.tier}
              </span>
            ) : null}
            {est !== null ? (
              <span className="text-[11px] text-muted">
                ~{formatCost(est)}
              </span>
            ) : null}
          </div>
          {card.bodyExcerpt ? (
            <p className="text-xs text-muted mt-1 line-clamp-2">
              {card.bodyExcerpt}
            </p>
          ) : null}
          {similar.length > 0 ? (
            <div className="mt-2 text-xs">
              <span className="text-amber-400/90">Similar to:</span>
              <ul className="mt-0.5 space-y-0.5">
                {similar.map(({ item, similarity }) => (
                  <li
                    key={item.id}
                    className="flex items-center gap-2 text-muted"
                  >
                    <span className="font-mono">{cardShortId(item)}</span>
                    <span className="overflow-hidden whitespace-nowrap text-ellipsis max-w-[320px]">
                      {cardTitle(item)}
                    </span>
                    <span className="text-[10px]">
                      {Math.round(similarity * 100)}%
                    </span>
                    <button
                      className="btn text-[11px] px-1.5 py-0.5"
                      disabled={anyBusy}
                      onClick={() => onMerge(item.id)}
                      title={`absorb this staged card into ${item.id}`}
                    >
                      Merge into
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            className="btn text-xs"
            disabled={anyBusy}
            onClick={onPromote}
            title="move to backlog"
          >
            {busy ? "..." : "Promote"}
          </button>
          <button
            className="btn text-xs"
            disabled={anyBusy}
            onClick={onDecline}
            title="park under _declined (recoverable)"
          >
            Decline
          </button>
        </div>
      </div>
    </li>
  );
}
