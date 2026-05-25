/**
 * Tiny fuzzy-match scorer used by the command palette. Not Sublime/VS
 * Code-grade -- this is a single-file scorer that handles the common
 * cases (substring, subsequence, case-insensitive) and ranks them
 * sensibly for a board with hundreds of cards, not tens of thousands.
 *
 * Returns a numeric score; -1 means "does not match." Higher is better.
 * Callers sort descending and take the top N.
 */

export interface FuzzyResult<T> {
  item: T;
  score: number;
  indices: number[];
}

export function fuzzyScore(query: string, target: string): {
  score: number;
  indices: number[];
} {
  if (query.length === 0) return { score: 0, indices: [] };
  if (target.length === 0) return { score: -1, indices: [] };

  const q = query.toLowerCase();
  const t = target.toLowerCase();

  // Substring match: highest scoring, earlier is better.
  const subIdx = t.indexOf(q);
  if (subIdx >= 0) {
    const indices: number[] = [];
    for (let i = 0; i < q.length; i++) indices.push(subIdx + i);
    return {
      score: 1000 - subIdx * 2 - (target.length - q.length),
      indices,
    };
  }

  // Subsequence match: each query char must appear in order. Bonuses
  // for adjacency and for hitting a word boundary.
  const indices: number[] = [];
  let ti = 0;
  let qi = 0;
  let score = 0;
  let lastIdx = -10;
  while (qi < q.length && ti < t.length) {
    if (q.charCodeAt(qi) === t.charCodeAt(ti)) {
      indices.push(ti);
      score += 10;
      if (ti === lastIdx + 1) score += 5; // adjacency
      const prev = t.charCodeAt(ti - 1);
      // word boundary: start of string, after space, dash, slash, or
      // dot. Cheap heuristic that works for `b042-runner-claim` etc.
      if (
        ti === 0 ||
        prev === 32 ||
        prev === 45 ||
        prev === 47 ||
        prev === 46 ||
        prev === 95
      )
        score += 8;
      lastIdx = ti;
      qi++;
    }
    ti++;
  }
  if (qi < q.length) return { score: -1, indices: [] };
  // Penalise long targets so a hit in a short id beats one in a long
  // title with the same chars scattered around.
  score -= Math.max(0, target.length - q.length) / 4;
  return { score, indices };
}

export function fuzzyRank<T>(
  query: string,
  items: readonly T[],
  pick: (item: T) => string,
  limit = 50
): FuzzyResult<T>[] {
  const out: FuzzyResult<T>[] = [];
  for (const item of items) {
    const { score, indices } = fuzzyScore(query, pick(item));
    if (score >= 0) out.push({ item, score, indices });
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, limit);
}
