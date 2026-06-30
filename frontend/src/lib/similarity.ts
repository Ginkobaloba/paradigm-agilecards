/**
 * Title similarity for triage dedup (roadmap 2.4, v1).
 *
 * Token-set Jaccard over normalized words. Symmetric on purpose: the
 * command palette's fuzzyScore answers "does this QUERY appear in that
 * TARGET", which is the wrong shape for "are these two titles about
 * the same work" -- a staged title rarely subsequence-matches an
 * existing one even when they describe the same card. Embedding
 * similarity is the horizon-3 upgrade (roadmap 3.6); word overlap is
 * the v1 the roadmap asks for.
 */

/** Words too common to carry signal between card titles. */
const STOPWORDS = new Set([
  "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
  "is", "it", "of", "on", "or", "the", "to", "with",
]);

export function titleTokens(title: string): Set<string> {
  const tokens = title
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/[\s-]+/)
    .filter((w) => w.length > 1 && !STOPWORDS.has(w));
  return new Set(tokens);
}

/** Jaccard similarity of the two titles' token sets, in [0, 1]. */
export function titleSimilarity(a: string, b: string): number {
  const ta = titleTokens(a);
  const tb = titleTokens(b);
  if (ta.size === 0 || tb.size === 0) return 0;
  let intersection = 0;
  for (const w of ta) if (tb.has(w)) intersection++;
  const union = ta.size + tb.size - intersection;
  return union === 0 ? 0 : intersection / union;
}

export interface SimilarItem<T> {
  item: T;
  similarity: number;
}

/**
 * Rank `items` by title similarity to `title`, keeping those at or
 * above `threshold`. 0.34 default: roughly "a third of the combined
 * vocabulary is shared", low enough to flag rephrasings, high enough
 * that one shared word ("add", filtered; "api", not) rarely fires
 * alone on realistic titles.
 */
export function rankSimilar<T>(
  title: string,
  items: readonly T[],
  pick: (item: T) => string,
  { threshold = 0.34, limit = 3 }: { threshold?: number; limit?: number } = {}
): SimilarItem<T>[] {
  const out: SimilarItem<T>[] = [];
  for (const item of items) {
    const similarity = titleSimilarity(title, pick(item));
    if (similarity >= threshold) out.push({ item, similarity });
  }
  out.sort((a, b) => b.similarity - a.similarity);
  return out.slice(0, limit);
}
