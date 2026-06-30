/**
 * Zustand slice for the submit-story surface.
 *
 * Kept separate from the cards store on purpose: the cards store is
 * the read-side projection of disk state, and components subscribe to
 * tiny slices of it. This store is the write-side flow -- it lives for
 * the duration of a single submit/approve cycle and gets reset on
 * completion or cancel.
 */

import { create } from "zustand";

export interface DryRunCard {
  readonly id: string;
  readonly title: string;
  readonly file: string;
  readonly tier: number | null;
  readonly model: string | null;
  readonly estimatedTokens: number | null;
  readonly dependsOn: ReadonlyArray<string>;
}

export interface DryRunPayload {
  readonly batchId: string;
  readonly cards: ReadonlyArray<DryRunCard>;
  readonly histogram: Readonly<Record<string, number>>;
  readonly dependsOnEdges: number;
  readonly claimableCount: number;
  readonly mode: "full" | "lean";
  readonly deepPlanning: boolean;
}

export interface ProgressEntry {
  readonly step: string;
  readonly agent: string;
  readonly message: string;
  readonly at: number;
}

export type Phase =
  | "idle"
  | "planning"
  | "dry_run"
  | "approving"
  | "complete"
  | "error";

interface SubmitState {
  phase: Phase;
  progress: ProgressEntry[];
  dryRun: DryRunPayload | null;
  errorMessage: string | null;
  errorStage: string | null;
  /** cardsWritten reported by the approve endpoint, surfaced on success */
  cardsWritten: number | null;

  startPlanning: () => void;
  pushProgress: (entry: Omit<ProgressEntry, "at">) => void;
  setDryRun: (payload: DryRunPayload) => void;
  setError: (message: string, stage: string | null) => void;
  beginApproval: () => void;
  finishApproval: (cardsWritten: number) => void;
  reset: () => void;
}

const initial = {
  phase: "idle" as Phase,
  progress: [] as ProgressEntry[],
  dryRun: null as DryRunPayload | null,
  errorMessage: null as string | null,
  errorStage: null as string | null,
  cardsWritten: null as number | null,
};

export const useSubmitStore = create<SubmitState>((set) => ({
  ...initial,

  startPlanning: () =>
    set(() => ({
      ...initial,
      phase: "planning",
    })),

  pushProgress: (entry) =>
    set((s) => ({
      progress: [...s.progress, { ...entry, at: Date.now() }],
    })),

  setDryRun: (payload) =>
    set(() => ({
      dryRun: payload,
      phase: "dry_run",
    })),

  setError: (message, stage) =>
    set(() => ({
      phase: "error",
      errorMessage: message,
      errorStage: stage,
    })),

  beginApproval: () =>
    set(() => ({
      phase: "approving",
    })),

  finishApproval: (cardsWritten) =>
    set(() => ({
      phase: "complete",
      cardsWritten,
    })),

  reset: () => set(() => ({ ...initial })),
}));
