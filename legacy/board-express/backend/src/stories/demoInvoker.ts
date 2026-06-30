/**
 * Demo invoker for the submit-story flow.
 *
 * The default `claudeCliInvoker` shells out to the `claude` CLI to run
 * the real /cards planner. That needs a live CLI plus model access,
 * which a fresh clone, a CI box, or a screenshot/demo run won't have.
 *
 * This invoker stands in for it: it emits a believable progress stream
 * and writes a small staged batch to disk so the dry-run review panel
 * renders end to end -- and so an `approve` still promotes real card
 * files into the backlog.
 *
 * It is wired in only when the `STORIES_DEMO_INVOKER` env var is set
 * (see server.ts). Production runs use the real invoker untouched.
 */

import fs from "node:fs";
import path from "node:path";

import type { Invoker } from "./invoker.js";
import type { Manifest, ManifestCardSummary } from "./manifest.js";
import { summarize } from "./manifest.js";
import { stagingDirFor } from "./staging.js";

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

interface DemoCard extends ManifestCardSummary {
  readonly stakes: string;
  readonly extendedThinking: boolean;
  readonly context: string;
  readonly scope: readonly string[];
}

/**
 * A believable three-card decomposition of the rate-limiting story the
 * submit page shows as placeholder text. Card ids are namespaced under
 * the batch id, exactly as the real planner namespaces them.
 */
function demoCards(batchId: string): DemoCard[] {
  return [
    {
      id: `${batchId}-01-pick-bucket-library`,
      title: "Pick a token-bucket library",
      file: `${batchId}-01-pick-bucket-library.md`,
      tier: 2,
      model: "claude-haiku-4-5",
      estimatedTokens: 14000,
      dependsOn: [],
      stakes: "low",
      extendedThinking: false,
      context:
        "Compare in-process token-bucket libraries and record the choice " +
        "so the middleware card has a settled dependency to build on.",
      scope: [
        "Evaluate two or three maintained token-bucket libraries.",
        "Write a short decision note with the pick and the reasoning.",
      ],
    },
    {
      id: `${batchId}-02-add-rate-limit-middleware`,
      title: "Add rate-limit middleware to the public API",
      file: `${batchId}-02-add-rate-limit-middleware.md`,
      tier: 3,
      model: "claude-sonnet-4-6",
      estimatedTokens: 22000,
      dependsOn: [`${batchId}-01-pick-bucket-library`],
      stakes: "medium",
      extendedThinking: false,
      context:
        "Wire the chosen library into the request path, keyed by API key, " +
        "with per-tier limits and a 429 plus Retry-After on overage.",
      scope: [
        "Add the middleware and register it in the documented order.",
        "Return 429 with a correct Retry-After header when a bucket is empty.",
        "Cover under-limit, at-limit, and over-limit cases with tests.",
      ],
    },
    {
      id: `${batchId}-03-wire-rate-limit-metrics`,
      title: "Emit rate-limit counters to the metrics pipeline",
      file: `${batchId}-03-wire-rate-limit-metrics.md`,
      tier: 3,
      model: "claude-sonnet-4-6",
      estimatedTokens: 18500,
      dependsOn: [`${batchId}-02-add-rate-limit-middleware`],
      stakes: "medium",
      extendedThinking: true,
      context:
        "Once the middleware exists, emit allow/deny counters so overage " +
        "is observable on the existing metrics dashboards.",
      scope: [
        "Increment a per-tier counter on every allow and deny decision.",
        "Add a dashboard panel and an alert on a sustained deny rate.",
      ],
    },
  ];
}

function renderStagedCard(
  c: DemoCard,
  opts: { projectPath: string | null; batchId: string }
): string {
  const fm = [
    "---",
    'verifier_schema_version: "1.3"',
    `id: ${c.id}`,
    `title: ${c.title}`,
    `project: ${opts.projectPath ?? "C:\\dev\\project-example"}`,
    "status: backlog",
    `points: ${c.tier}`,
    `stakes: ${c.stakes}`,
    `model: ${c.model}`,
    `extended_thinking: ${c.extendedThinking}`,
    `estimated_tokens: ${c.estimatedTokens}`,
    `depends_on: [${c.dependsOn.join(", ")}]`,
    `batch: ${opts.batchId}`,
    `created: ${new Date().toISOString().slice(0, 10)}`,
    "---",
  ].join("\n");
  const body = [
    "## Context",
    "",
    c.context,
    "",
    "## Scope",
    "",
    ...c.scope.map((s) => `- ${s}`),
    "",
  ].join("\n");
  return `${fm}\n\n${body}`;
}

export const demoInvoker: Invoker = async (opts, onProgress) => {
  const staging = stagingDirFor(opts.batchId);
  fs.mkdirSync(staging, { recursive: true });

  const steps: ReadonlyArray<{
    step: "planning" | "review" | "writing";
    agent: string;
    message: string;
  }> = [
    { step: "planning", agent: "planner", message: "reading the story and the project config" },
    { step: "planning", agent: "planner", message: "decomposing the story into candidate cards" },
    { step: "planning", agent: "planner", message: "sizing each card against the tier rubric" },
    { step: "review", agent: "reviewer", message: "checking for hidden coupling between cards" },
    { step: "review", agent: "reviewer", message: "confirming depends_on edges form a DAG" },
    { step: "writing", agent: "cards", message: `staging batch ${opts.batchId} for dry-run review` },
  ];
  for (const s of steps) {
    onProgress(s);
    // A small pause so the progress stream looks alive on the wire.
    await delay(220);
  }

  const cards = demoCards(opts.batchId);
  for (const c of cards) {
    fs.writeFileSync(
      path.join(staging, c.file),
      renderStagedCard(c, { projectPath: opts.projectPath, batchId: opts.batchId }),
      "utf8"
    );
  }

  const summaries: ManifestCardSummary[] = cards.map((c) => ({
    id: c.id,
    title: c.title,
    file: c.file,
    tier: c.tier,
    model: c.model,
    estimatedTokens: c.estimatedTokens,
    dependsOn: c.dependsOn,
  }));

  const manifest: Manifest = {
    batchId: opts.batchId,
    story: opts.story,
    projectPath: opts.projectPath,
    mode: opts.mode,
    deepPlanning: opts.deepPlanning,
    cards: summaries,
    ...summarize(summaries),
  };

  fs.writeFileSync(
    path.join(staging, "manifest.json"),
    JSON.stringify(manifest, null, 2),
    "utf8"
  );

  return { manifest };
};
