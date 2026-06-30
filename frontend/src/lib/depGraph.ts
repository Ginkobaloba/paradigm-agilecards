/**
 * Dependency graph computation for the kanban's dep-view modal.
 *
 * Inputs are the cards visible after filtering. Outputs are:
 *   - per-card depth (0 = no deps among visible cards; N = deepest
 *     chain of `depends_on` edges that lands inside the visible set)
 *   - the set of cards involved in any cycle
 *   - the columns-by-depth layout the modal renders
 *
 * Why "within the visible set": dependencies on cards that have been
 * filtered out (or removed) are still listed on the source card's
 * "external deps" line, but they do not pull the visible card to a
 * deeper depth. This keeps the layout stable as the operator narrows
 * filters.
 */

import type { CardSummary } from "./api";

import { cardDependsOn } from "./parseCard";

export interface DepNode {
  readonly card: CardSummary;
  readonly depth: number;
  /** ids of visible deps this card depends on directly */
  readonly visibleDeps: readonly string[];
  /** ids of deps that exist but aren't in the visible set */
  readonly externalDeps: readonly string[];
  readonly inCycle: boolean;
}

export interface DepLayout {
  readonly nodes: ReadonlyMap<string, DepNode>;
  /** cards grouped by depth, index = depth */
  readonly columns: ReadonlyArray<readonly DepNode[]>;
  /** ids of cards in any cycle */
  readonly cycleIds: ReadonlySet<string>;
}

/**
 * Compute the depth-layered layout of a card set's dependency graph.
 * Algorithm: iterative longest-path on a DAG with cycle detection via
 * Tarjan's SCC.
 *   1. Build adjacency from `depends_on` filtered to visible cards.
 *   2. Find SCCs; SCCs with >1 node (or a single node with a self-loop)
 *      are cycles. Cards in cycles get a fixed depth (max of their
 *      acyclic-resolved depth) and an inCycle flag.
 *   3. Compute depth by post-order DFS over the condensed DAG.
 */
export function computeDepLayout(
  cards: readonly CardSummary[]
): DepLayout {
  const visibleSet = new Set(cards.map((c) => c.id));
  const cardById = new Map<string, CardSummary>();
  for (const c of cards) cardById.set(c.id, c);

  const depsOf = new Map<string, string[]>();
  const externalsOf = new Map<string, string[]>();
  for (const c of cards) {
    const ds = cardDependsOn(c);
    const inside: string[] = [];
    const outside: string[] = [];
    for (const d of ds) {
      if (visibleSet.has(d)) inside.push(d);
      else outside.push(d);
    }
    depsOf.set(c.id, inside);
    externalsOf.set(c.id, outside);
  }

  const cycleIds = findCycleNodes(cards.map((c) => c.id), depsOf);

  // Depth via memoized DFS. Self-loops or cyclic deps return depth=0
  // for the cycle members themselves; their downstream non-cycle deps
  // still resolve correctly.
  const depth = new Map<string, number>();
  const visiting = new Set<string>();

  function depthOf(id: string): number {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) {
      // Hit a back-edge mid-computation: treat as 0 for this branch.
      // The cycle pass below will pin the canonical depth.
      return 0;
    }
    visiting.add(id);
    let d = 0;
    for (const dep of depsOf.get(id) ?? []) {
      const dd = depthOf(dep);
      if (dd + 1 > d) d = dd + 1;
    }
    visiting.delete(id);
    depth.set(id, d);
    return d;
  }

  for (const c of cards) depthOf(c.id);

  const nodes = new Map<string, DepNode>();
  for (const c of cards) {
    nodes.set(c.id, {
      card: c,
      depth: depth.get(c.id) ?? 0,
      visibleDeps: depsOf.get(c.id) ?? [],
      externalDeps: externalsOf.get(c.id) ?? [],
      inCycle: cycleIds.has(c.id),
    });
  }

  const maxDepth = Math.max(0, ...Array.from(depth.values()));
  const columns: DepNode[][] = Array.from({ length: maxDepth + 1 }, () => []);
  for (const node of nodes.values()) {
    columns[node.depth]!.push(node);
  }
  // Stable ordering within a column: by id, deterministic.
  for (const col of columns) {
    col.sort((a, b) => a.card.id.localeCompare(b.card.id));
  }

  return { nodes, columns, cycleIds };
}

/**
 * Identify all nodes participating in a cycle (including self-loops).
 * Tarjan-style iterative SCC.
 */
function findCycleNodes(
  ids: readonly string[],
  depsOf: ReadonlyMap<string, readonly string[]>
): Set<string> {
  const cycleIds = new Set<string>();

  const index = new Map<string, number>();
  const lowlink = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  let counter = 0;

  function strongconnect(start: string): void {
    type Frame = { id: string; iter: number };
    const frames: Frame[] = [{ id: start, iter: 0 }];

    index.set(start, counter);
    lowlink.set(start, counter);
    counter++;
    stack.push(start);
    onStack.add(start);

    while (frames.length > 0) {
      const frame = frames[frames.length - 1]!;
      const deps = depsOf.get(frame.id) ?? [];
      if (frame.iter < deps.length) {
        const dep = deps[frame.iter]!;
        frame.iter++;
        if (!index.has(dep)) {
          index.set(dep, counter);
          lowlink.set(dep, counter);
          counter++;
          stack.push(dep);
          onStack.add(dep);
          frames.push({ id: dep, iter: 0 });
        } else if (onStack.has(dep)) {
          lowlink.set(
            frame.id,
            Math.min(lowlink.get(frame.id)!, index.get(dep)!)
          );
        }
      } else {
        // Done with frame.id's neighbors. Pop and propagate lowlink.
        frames.pop();
        if (frames.length > 0) {
          const parent = frames[frames.length - 1]!;
          lowlink.set(
            parent.id,
            Math.min(lowlink.get(parent.id)!, lowlink.get(frame.id)!)
          );
        }
        // Root of an SCC?
        if (lowlink.get(frame.id) === index.get(frame.id)) {
          const scc: string[] = [];
          let popped: string | undefined;
          do {
            popped = stack.pop();
            if (popped === undefined) break;
            onStack.delete(popped);
            scc.push(popped);
          } while (popped !== frame.id);
          // SCC is a cycle iff size > 1 OR a self-loop on a singleton.
          if (scc.length > 1) {
            for (const n of scc) cycleIds.add(n);
          } else if (scc.length === 1) {
            const only = scc[0]!;
            if ((depsOf.get(only) ?? []).includes(only)) {
              cycleIds.add(only);
            }
          }
        }
      }
    }
  }

  for (const id of ids) {
    if (!index.has(id)) strongconnect(id);
  }

  return cycleIds;
}

/**
 * Count downstream dependents for a single card -- "how many other
 * cards in the visible set are blocked by this one (transitively)?"
 * The roadmap calls this the "which card unblocks the most?" question.
 */
export function countDependents(
  cardId: string,
  layout: DepLayout
): number {
  // Build reverse adjacency once per lookup; for big graphs we could
  // memoize but the dep-view modal is bounded by the filtered card set.
  const inbound = new Map<string, Set<string>>();
  for (const node of layout.nodes.values()) {
    for (const d of node.visibleDeps) {
      if (!inbound.has(d)) inbound.set(d, new Set());
      inbound.get(d)!.add(node.card.id);
    }
  }
  const seen = new Set<string>();
  const stack = [cardId];
  while (stack.length > 0) {
    const cur = stack.pop()!;
    const downs = inbound.get(cur);
    if (!downs) continue;
    for (const d of downs) {
      if (seen.has(d)) continue;
      seen.add(d);
      stack.push(d);
    }
  }
  return seen.size;
}
