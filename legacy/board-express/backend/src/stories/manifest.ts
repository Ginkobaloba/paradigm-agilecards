/**
 * Manifest shape produced by the /cards skill. Mirrors the
 * `_batches/<batch_id>/manifest.json` that the skill writes today.
 *
 * Kept here (rather than in the route) so the invoker, the staging
 * promoter, and the SSE serializer all narrow against the same type.
 * If the skill changes its on-disk shape, this is the one place to
 * widen.
 */

export interface ManifestCardSummary {
  /** Card id, e.g. "b042-03-add-rate-limit-middleware". */
  readonly id: string;
  /** Human title, pulled from the card's frontmatter. */
  readonly title: string;
  /** Filename in the staging dir, basename only. */
  readonly file: string;
  /** Tier number, 1-6. Null if the planner didn't assign one. */
  readonly tier: number | null;
  /** Model name string from frontmatter, e.g. "claude-sonnet-4-6". */
  readonly model: string | null;
  /** Estimated tokens for the card body+plan. Null if not estimated. */
  readonly estimatedTokens: number | null;
  /** Ids this card depends on. Empty array if it has no deps. */
  readonly dependsOn: ReadonlyArray<string>;
}

export interface Manifest {
  /** Globally unique batch id, e.g. "b042". */
  readonly batchId: string;
  /** Originating story, verbatim, so the manifest is self-contained. */
  readonly story: string;
  /** Optional project path the story targets. */
  readonly projectPath: string | null;
  /** Planning mode used. */
  readonly mode: "full" | "lean";
  /** Whether the 3-agent variant was forced via --deep. */
  readonly deepPlanning: boolean;
  /** All cards produced, in planner order. */
  readonly cards: ReadonlyArray<ManifestCardSummary>;
  /** Tier histogram: { "1": 0, "2": 3, ... }. Tiers with zero are omitted. */
  readonly histogram: Readonly<Record<string, number>>;
  /** Count of depends_on edges across the batch. */
  readonly dependsOnEdges: number;
  /** Cards with no unresolved deps -- ready for the runner to claim. */
  readonly claimableCount: number;
}

/**
 * Build a histogram + claimable count from a list of cards. Useful
 * when the on-disk manifest is missing fields the dry-run UI needs
 * (older skill versions only wrote the card list).
 */
export function summarize(
  cards: ReadonlyArray<ManifestCardSummary>
): Pick<Manifest, "histogram" | "dependsOnEdges" | "claimableCount"> {
  const histogram: Record<string, number> = {};
  let edges = 0;
  let claimable = 0;
  const ids = new Set(cards.map((c) => c.id));

  for (const c of cards) {
    const key = c.tier !== null ? String(c.tier) : "?";
    histogram[key] = (histogram[key] ?? 0) + 1;
    edges += c.dependsOn.length;
    const blockedByInBatch = c.dependsOn.some((d) => ids.has(d));
    if (!blockedByInBatch) claimable += 1;
  }

  return { histogram, dependsOnEdges: edges, claimableCount: claimable };
}
