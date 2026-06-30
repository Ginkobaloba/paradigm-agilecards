/**
 * Modal form for creating or editing a sprint. Used by the planner list
 * (create mode) and the sprint detail page (edit mode -- ships in PR
 * B). Keeps validation logic in one place.
 */

import * as Dialog from "@radix-ui/react-dialog";
import { type FormEvent, useEffect, useState } from "react";

import {
  ApiError,
  type Sprint,
  type SprintCreate,
  type SprintPatch,
  type SprintStatus,
} from "../lib/api";

interface CreateProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (body: SprintCreate) => Promise<void>;
  mode: "create";
}

interface EditProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (patch: SprintPatch) => Promise<void>;
  mode: "edit";
  sprint: Sprint;
}

type Props = CreateProps | EditProps;

export function SprintFormDialog(props: Props) {
  const initial = props.mode === "edit" ? props.sprint : null;
  const [name, setName] = useState(initial?.name ?? "");
  const [startsAt, setStartsAt] = useState(
    (initial?.startsAt ?? "").slice(0, 10)
  );
  const [endsAt, setEndsAt] = useState(
    (initial?.endsAt ?? "").slice(0, 10)
  );
  const [goal, setGoal] = useState(initial?.goal ?? "");
  const [status, setStatus] = useState<SprintStatus>(
    initial?.status ?? "planning"
  );
  const [pointsTarget, setPointsTarget] = useState<string>(
    initial?.pointsTarget !== null && initial?.pointsTarget !== undefined
      ? String(initial.pointsTarget)
      : ""
  );
  const [dollarTarget, setDollarTarget] = useState<string>(
    initial?.dollarTarget !== null && initial?.dollarTarget !== undefined
      ? String(initial.dollarTarget)
      : ""
  );
  const [reviewHoursTarget, setReviewHoursTarget] = useState<string>(
    initial?.reviewHoursTarget !== null &&
      initial?.reviewHoursTarget !== undefined
      ? String(initial.reviewHoursTarget)
      : ""
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!props.open) return;
    setError(null);
    setSubmitting(false);
    if (props.mode === "edit") {
      setName(props.sprint.name);
      setStartsAt(props.sprint.startsAt.slice(0, 10));
      setEndsAt(props.sprint.endsAt.slice(0, 10));
      setGoal(props.sprint.goal ?? "");
      setStatus(props.sprint.status);
      setPointsTarget(
        props.sprint.pointsTarget !== null
          ? String(props.sprint.pointsTarget)
          : ""
      );
      setDollarTarget(
        props.sprint.dollarTarget !== null
          ? String(props.sprint.dollarTarget)
          : ""
      );
      setReviewHoursTarget(
        props.sprint.reviewHoursTarget !== null
          ? String(props.sprint.reviewHoursTarget)
          : ""
      );
    } else {
      setName("");
      setStartsAt(defaultStart());
      setEndsAt(defaultEnd());
      setGoal("");
      setStatus("planning");
      setPointsTarget("");
      setDollarTarget("");
      setReviewHoursTarget("");
    }
  }, [props]);

  const onSubmitForm = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!startsAt || !endsAt) {
      setError("Both start and end dates are required.");
      return;
    }
    if (endsAt < startsAt) {
      setError("End date cannot be before start date.");
      return;
    }
    setSubmitting(true);
    try {
      if (props.mode === "create") {
        await props.onSubmit({
          name: name.trim(),
          startsAt,
          endsAt,
          goal: goal.trim() ? goal.trim() : null,
        });
      } else {
        await props.onSubmit({
          name: name.trim(),
          startsAt,
          endsAt,
          goal: goal.trim() ? goal.trim() : null,
          status,
          pointsTarget: parseTargetInt(pointsTarget),
          dollarTarget: parseTargetFloat(dollarTarget),
          reviewHoursTarget: parseTargetFloat(reviewHoursTarget),
        });
      }
    } catch (err: unknown) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog.Root
      open={props.open}
      onOpenChange={(o) => !o && !submitting && props.onClose()}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className="surface fixed left-1/2 top-[15vh] z-50 w-[min(540px,92vw)] -translate-x-1/2 flex flex-col"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <Dialog.Title className="text-sm font-semibold text-text">
              {props.mode === "create" ? "New sprint" : "Edit sprint"}
            </Dialog.Title>
            <Dialog.Close className="btn" aria-label="Close">
              Close
            </Dialog.Close>
          </div>
          <form className="flex flex-col gap-3 p-4" onSubmit={onSubmitForm}>
            <FieldRow label="Name">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                autoFocus
                required
              />
            </FieldRow>
            <div className="grid grid-cols-2 gap-3">
              <FieldRow label="Starts">
                <input
                  type="date"
                  value={startsAt}
                  onChange={(e) => setStartsAt(e.target.value)}
                  className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                  required
                />
              </FieldRow>
              <FieldRow label="Ends">
                <input
                  type="date"
                  value={endsAt}
                  onChange={(e) => setEndsAt(e.target.value)}
                  className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                  required
                />
              </FieldRow>
            </div>
            <FieldRow label="Goal">
              <textarea
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                rows={3}
                className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                placeholder="What does success look like for this sprint?"
              />
            </FieldRow>
            {props.mode === "edit" ? (
              <>
                <FieldRow label="Status">
                  <select
                    value={status}
                    onChange={(e) => setStatus(e.target.value as SprintStatus)}
                    className="rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                  >
                    <option value="planning">planning</option>
                    <option value="active">active</option>
                    <option value="completed">completed</option>
                    <option value="cancelled">cancelled</option>
                  </select>
                </FieldRow>
                <div className="grid grid-cols-3 gap-3">
                  <FieldRow label="Points target">
                    <input
                      type="number"
                      min={0}
                      step={1}
                      value={pointsTarget}
                      onChange={(e) => setPointsTarget(e.target.value)}
                      className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                      placeholder="—"
                    />
                  </FieldRow>
                  <FieldRow label="$ target">
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={dollarTarget}
                      onChange={(e) => setDollarTarget(e.target.value)}
                      className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                      placeholder="—"
                    />
                  </FieldRow>
                  <FieldRow label="Review hrs">
                    <input
                      type="number"
                      min={0}
                      step={0.5}
                      value={reviewHoursTarget}
                      onChange={(e) => setReviewHoursTarget(e.target.value)}
                      className="w-full rounded border border-border bg-panel2 px-2 py-1 text-[12px] text-text focus:border-accent focus:outline-none"
                      placeholder="—"
                    />
                  </FieldRow>
                </div>
              </>
            ) : null}

            {error ? (
              <div className="rounded border border-danger/30 bg-danger/10 px-2 py-1 text-[11px] text-danger">
                {error}
              </div>
            ) : null}

            <div className="mt-2 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => !submitting && props.onClose()}
                className="rounded border border-border bg-panel2 px-3 py-1 text-[11px] text-muted hover:text-text"
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded border border-accent/60 bg-accent/10 px-3 py-1 text-[11px] font-semibold text-accent hover:bg-accent/20 disabled:opacity-50"
                disabled={submitting}
              >
                {submitting
                  ? "Saving..."
                  : props.mode === "create"
                    ? "Create sprint"
                    : "Save"}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function FieldRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 text-[11px] text-muted">
      <span className="uppercase tracking-wider text-[10px] opacity-70">
        {label}
      </span>
      {children}
    </label>
  );
}

/**
 * Empty string -> null (no target). Otherwise parse an integer; non-
 * finite or negative -> null. Used for points target.
 */
function parseTargetInt(s: string): number | null {
  if (s.trim() === "") return null;
  const n = parseInt(s, 10);
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}

/**
 * Same as parseTargetInt but for float targets ($ and review hours).
 */
function parseTargetFloat(s: string): number | null {
  if (s.trim() === "") return null;
  const n = parseFloat(s);
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}

function defaultStart(): string {
  // Today in YYYY-MM-DD (local).
  return new Date().toISOString().slice(0, 10);
}

function defaultEnd(): string {
  // Two weeks from today.
  const d = new Date();
  d.setDate(d.getDate() + 14);
  return d.toISOString().slice(0, 10);
}
