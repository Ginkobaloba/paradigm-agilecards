/**
 * /submit route. Paste a story, run the /cards planner, review the
 * dry-run, approve or cancel.
 *
 * Kept deliberately spartan for v1: a single column layout, no modal,
 * no animations. Once the wire-up is settled we can polish.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  approveBatch,
  cancelBatch,
  streamSubmit,
  SubmitError,
  type SseEvent,
} from "../lib/submitStory";
import {
  useSubmitStore,
  type DryRunPayload,
  type ProgressEntry,
} from "../state/submitStore";
import { tierBadgeClass } from "../lib/tierBadge";

const PLACEHOLDER = [
  "Example:",
  "As an operator I want to rate-limit the public API so a single",
  "misbehaving client can't degrade latency for everyone else. Tiered",
  "limits by API key (free 60/min, pro 600/min, enterprise 6000/min).",
  "Token-bucket strategy. 429 with Retry-After on overage.",
].join("\n");

interface ProjectOption {
  readonly value: string;
  readonly label: string;
}

const PROJECT_OPTIONS: ProjectOption[] = [
  { value: "", label: "(no project)" },
  { value: "C:\\dev\\agile-cards", label: "agile-cards" },
  { value: "C:\\dev\\agile-cards-board", label: "agile-cards-board" },
  { value: "C:\\dev\\project-example", label: "project-example" },
];

export function SubmitStory() {
  const navigate = useNavigate();
  const phase = useSubmitStore((s) => s.phase);
  const progress = useSubmitStore((s) => s.progress);
  const dryRun = useSubmitStore((s) => s.dryRun);
  const errorMessage = useSubmitStore((s) => s.errorMessage);
  const errorStage = useSubmitStore((s) => s.errorStage);
  const cardsWritten = useSubmitStore((s) => s.cardsWritten);
  const start = useSubmitStore((s) => s.startPlanning);
  const pushProgress = useSubmitStore((s) => s.pushProgress);
  const setDryRun = useSubmitStore((s) => s.setDryRun);
  const setError = useSubmitStore((s) => s.setError);
  const beginApproval = useSubmitStore((s) => s.beginApproval);
  const finishApproval = useSubmitStore((s) => s.finishApproval);
  const reset = useSubmitStore((s) => s.reset);

  const [story, setStory] = useState("");
  const [projectPath, setProjectPath] = useState<string>("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [deepPlanning, setDeepPlanning] = useState(false);
  const [modeOverride, setModeOverride] = useState<"" | "full" | "lean">("");

  const abortRef = useRef<AbortController | null>(null);

  // Reset the store when the user leaves the page. Otherwise stale
  // progress shows up on next visit.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      reset();
    };
  }, [reset]);

  const canPlan = story.trim().length > 0 && phase === "idle";
  const isStreaming = phase === "planning";
  const showDryRun = phase === "dry_run" && dryRun !== null;
  const showError = phase === "error" && errorMessage !== null;
  const isApproving = phase === "approving";
  const isComplete = phase === "complete";

  const onPlan = async (): Promise<void> => {
    if (!canPlan) return;
    start();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      for await (const evt of streamSubmit(
        {
          story,
          projectPath: projectPath.length > 0 ? projectPath : null,
          mode: modeOverride === "" ? null : modeOverride,
          deepPlanning,
        },
        ctrl.signal
      )) {
        handleEvent(evt, { pushProgress, setDryRun, setError });
      }
    } catch (err) {
      if (ctrl.signal.aborted) return;
      const msg = err instanceof Error ? err.message : String(err);
      const stage = err instanceof SubmitError ? err.stage : null;
      setError(msg, stage);
    }
  };

  const onApprove = async (): Promise<void> => {
    if (!dryRun) return;
    beginApproval();
    try {
      const result = await approveBatch(dryRun.batchId);
      finishApproval(result.cardsWritten);
      // Give the chokidar watcher a beat to fire card-added events on
      // the live /events stream, then hand the user back to the board.
      window.setTimeout(() => navigate("/"), 600);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const stage = err instanceof SubmitError ? err.stage : "approve";
      setError(msg, stage);
    }
  };

  const onCancel = async (): Promise<void> => {
    if (!dryRun) {
      reset();
      return;
    }
    await cancelBatch(dryRun.batchId);
    reset();
  };

  const onAbort = (): void => {
    abortRef.current?.abort();
    reset();
  };

  return (
    <div className="flex flex-col gap-4 px-5 py-4 max-w-4xl mx-auto">
      <header>
        <h2 className="text-base font-semibold text-text">Submit a story</h2>
        <p className="text-xs text-muted mt-1">
          Paste a user story below. The /cards planner decomposes it into
          claimable cards and shows you a dry-run before anything lands in
          the backlog.
        </p>
      </header>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-muted">Story</span>
        <textarea
          aria-label="story"
          className="input min-h-[180px] font-mono text-[13px] leading-relaxed"
          placeholder={PLACEHOLDER}
          value={story}
          onChange={(e) => setStory(e.target.value)}
          disabled={isStreaming || isApproving}
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs text-muted">Project</span>
        <select
          aria-label="project"
          className="input"
          value={projectPath}
          onChange={(e) => setProjectPath(e.target.value)}
          disabled={isStreaming || isApproving}
        >
          {PROJECT_OPTIONS.map((p) => (
            <option key={p.value || "none"} value={p.value}>
              {p.label}
              {p.value ? `  -  ${p.value}` : ""}
            </option>
          ))}
        </select>
      </label>

      <details
        className="surface px-3 py-2"
        open={showAdvanced}
        onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}
      >
        <summary className="text-xs text-muted cursor-pointer select-none">
          Advanced options
        </summary>
        <div className="flex flex-col gap-2 pt-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={deepPlanning}
              onChange={(e) => setDeepPlanning(e.target.checked)}
              disabled={isStreaming || isApproving}
            />
            <span>Deep planning (3-agent variant)</span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <span className="w-28 text-muted text-xs">Mode override</span>
            <select
              className="input flex-1"
              value={modeOverride}
              onChange={(e) =>
                setModeOverride(e.target.value as "" | "full" | "lean")
              }
              disabled={isStreaming || isApproving}
            >
              <option value="">(use project config)</option>
              <option value="full">full</option>
              <option value="lean">lean</option>
            </select>
          </label>
        </div>
      </details>

      <div className="flex items-center gap-2">
        <button
          className="btn btn-primary disabled:opacity-50"
          disabled={!canPlan}
          onClick={() => void onPlan()}
        >
          {isStreaming ? "Planning…" : "Plan this story"}
        </button>
        {isStreaming ? (
          <button className="btn" onClick={onAbort}>
            Abort
          </button>
        ) : null}
        {isComplete ? (
          <span className="text-xs text-ok">
            wrote {cardsWritten} card{cardsWritten === 1 ? "" : "s"} to
            backlog. Returning to the kanban…
          </span>
        ) : null}
      </div>

      {progress.length > 0 ? <ProgressPanel entries={progress} /> : null}

      {showError ? (
        <div className="surface p-3 text-xs text-danger">
          <strong>error</strong>
          {errorStage ? <span className="text-muted"> at {errorStage}</span> : null}
          <div className="mt-1 font-mono whitespace-pre-wrap">{errorMessage}</div>
          <button className="btn mt-3" onClick={reset}>
            Reset
          </button>
        </div>
      ) : null}

      {showDryRun ? (
        <DryRunReview
          payload={dryRun}
          approving={isApproving}
          onApprove={() => void onApprove()}
          onCancel={() => void onCancel()}
        />
      ) : null}
    </div>
  );
}

function handleEvent(
  evt: SseEvent,
  sinks: {
    pushProgress: (e: { step: string; agent: string; message: string }) => void;
    setDryRun: (p: DryRunPayload) => void;
    setError: (msg: string, stage: string | null) => void;
  }
): void {
  switch (evt.event) {
    case "progress": {
      const d = evt.data as
        | { step?: unknown; agent?: unknown; message?: unknown }
        | null;
      if (!d || typeof d !== "object") return;
      sinks.pushProgress({
        step: typeof d.step === "string" ? d.step : "info",
        agent: typeof d.agent === "string" ? d.agent : "?",
        message: typeof d.message === "string" ? d.message : "",
      });
      return;
    }
    case "dry_run": {
      const d = evt.data as Record<string, unknown> | null;
      if (!d || typeof d !== "object") return;
      const cards = Array.isArray(d["cards"]) ? d["cards"] : [];
      sinks.setDryRun({
        batchId: typeof d["batch_id"] === "string" ? (d["batch_id"] as string) : "",
        cards: cards
          .filter((c): c is Record<string, unknown> => c !== null && typeof c === "object")
          .map((c) => ({
            id: typeof c["id"] === "string" ? c["id"] : "(unknown)",
            title: typeof c["title"] === "string" ? c["title"] : "(untitled)",
            file: typeof c["file"] === "string" ? c["file"] : "",
            tier:
              typeof c["tier"] === "number"
                ? c["tier"]
                : typeof c["points"] === "number"
                  ? (c["points"] as number)
                  : null,
            model: typeof c["model"] === "string" ? c["model"] : null,
            estimatedTokens:
              typeof c["estimatedTokens"] === "number"
                ? c["estimatedTokens"]
                : typeof c["estimated_tokens"] === "number"
                  ? (c["estimated_tokens"] as number)
                  : null,
            dependsOn: Array.isArray(c["dependsOn"])
              ? (c["dependsOn"] as unknown[]).filter(
                  (x): x is string => typeof x === "string"
                )
              : Array.isArray(c["depends_on"])
                ? (c["depends_on"] as unknown[]).filter(
                    (x): x is string => typeof x === "string"
                  )
                : [],
          })),
        histogram:
          typeof d["histogram"] === "object" && d["histogram"] !== null
            ? coerceNumberMap(d["histogram"] as Record<string, unknown>)
            : {},
        dependsOnEdges:
          typeof d["depends_on_edges"] === "number"
            ? (d["depends_on_edges"] as number)
            : 0,
        claimableCount:
          typeof d["claimable_count"] === "number"
            ? (d["claimable_count"] as number)
            : 0,
        mode: d["mode"] === "lean" ? "lean" : "full",
        deepPlanning: d["deep_planning"] === true,
      });
      return;
    }
    case "error": {
      const d = evt.data as { message?: unknown; stage?: unknown } | null;
      if (!d || typeof d !== "object") return;
      sinks.setError(
        typeof d.message === "string" ? d.message : "unknown error",
        typeof d.stage === "string" ? d.stage : null
      );
      return;
    }
    default:
      return;
  }
}

function coerceNumberMap(
  m: Record<string, unknown>
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(m)) {
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

function ProgressPanel({ entries }: { entries: ProgressEntry[] }) {
  return (
    <div className="surface p-3 text-xs text-text font-mono leading-relaxed max-h-60 overflow-auto">
      {entries.map((e, i) => (
        <div key={i} className="flex gap-2">
          <span className="text-muted">[{e.step}]</span>
          <span className="text-accent">{e.agent}</span>
          <span className="text-muted">·</span>
          <span>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

function DryRunReview({
  payload,
  approving,
  onApprove,
  onCancel,
}: {
  payload: DryRunPayload;
  approving: boolean;
  onApprove: () => void;
  onCancel: () => void;
}) {
  const totalTokens = useMemo(() => {
    return payload.cards.reduce(
      (acc, c) => acc + (c.estimatedTokens ?? 0),
      0
    );
  }, [payload.cards]);

  return (
    <section className="surface p-4 flex flex-col gap-3">
      <header className="flex items-baseline gap-3">
        <h3 className="text-sm font-semibold text-text">
          Dry-run review
          <span className="text-muted font-normal ml-2 text-xs">
            batch {payload.batchId}
          </span>
        </h3>
        <span className="text-xs text-muted">
          {payload.cards.length} cards · {payload.dependsOnEdges} deps ·{" "}
          {payload.claimableCount} claimable · {totalTokens.toLocaleString()} est.
          tokens
        </span>
      </header>

      <div className="flex flex-wrap gap-1.5">
        {Object.entries(payload.histogram).map(([tier, count]) => (
          <span
            key={tier}
            className="flex items-center gap-1.5 rounded border border-border bg-panel2 px-2 py-0.5 text-[11px] text-muted"
            title={`tier ${tier}`}
          >
            <span
              className={`inline-block h-2.5 w-2.5 rounded-sm ${tierBadgeClass(
                Number(tier)
              )}`}
            />
            tier {tier}
            <span className="font-semibold text-text">{count}</span>
          </span>
        ))}
      </div>

      <ul className="flex flex-col gap-1.5">
        {payload.cards.map((c) => (
          <li
            key={c.id}
            className="bg-panel2 border border-border rounded px-3 py-2 text-xs"
          >
            <div className="flex items-center gap-2">
              {c.tier !== null ? (
                <span
                  className={[
                    "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded",
                    "text-[10px] font-semibold text-bg",
                    tierBadgeClass(c.tier),
                  ].join(" ")}
                  title={`tier ${c.tier}`}
                >
                  {c.tier}
                </span>
              ) : null}
              <span className="font-semibold text-text">{c.title}</span>
              {c.model ? (
                <span className="font-mono text-[10px] text-muted">
                  {c.model}
                </span>
              ) : null}
              {c.estimatedTokens !== null ? (
                <span className="text-[10px] text-muted ml-auto">
                  ~{c.estimatedTokens.toLocaleString()} tok
                </span>
              ) : null}
            </div>
            <div className="text-muted mt-0.5 font-mono">{c.id}</div>
            {c.dependsOn.length > 0 ? (
              <div className="text-muted mt-0.5">
                depends on:{" "}
                <span className="font-mono">{c.dependsOn.join(", ")}</span>
              </div>
            ) : null}
          </li>
        ))}
      </ul>

      <div className="flex gap-2">
        <button
          className="btn btn-primary disabled:opacity-50"
          onClick={onApprove}
          disabled={approving}
        >
          {approving ? "Writing to backlog…" : "Approve and write to backlog"}
        </button>
        <button className="btn" onClick={onCancel} disabled={approving}>
          Cancel
        </button>
      </div>
    </section>
  );
}
