/**
 * /cards skill invoker.
 *
 * == Decision: shell out to `claude` CLI ==
 *
 * Two paths to invoke the planner from the backend:
 *
 *   (a) Shell out to the `claude` CLI in headless mode and let the
 *       installed skill do its thing. The CLI already knows about
 *       agents, tool permissions, MCP servers, and the project's
 *       configured Claude model. No SDK key handling here.
 *
 *   (b) Call the Anthropic SDK directly, load SKILL.md as a system
 *       prompt, and reimplement the agent-loop logic ourselves.
 *
 * For v1 we ship (a). Reasons:
 *
 *   - The /cards skill is the canonical entry point Drew already uses
 *     from his terminal. The dashboard should be a different mouth on
 *     the same animal, not a parallel reimplementation.
 *   - Avoids duplicating SKILL.md's prompt logic + the planner/reviewer
 *     agent orchestration in TypeScript.
 *   - The CLI's tool/permission model is already battle-tested. We don't
 *     want the backend re-deriving which file paths the planner is
 *     allowed to touch.
 *   - A separate runner design pass is in flight. When it lands a final
 *     invocation contract, swapping the implementation behind this
 *     `Invoker` interface is a one-file change.
 *
 * The invoker is an injectable function so tests can pass a fake that
 * writes a stub manifest synchronously without spawning anything. The
 * default implementation spawns `claude` and routes its stdout lines
 * through the SSE bridge.
 *
 * Skill output redirection: the prompt explicitly tells the skill to
 * write into the staging dir instead of the live backlog. Until the
 * skill supports a first-class `--output-root` flag, the prompt is the
 * contract. The runner design pass is expected to land a cleaner
 * mechanism.
 */

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { log } from "../logger.js";
import type { Manifest } from "./manifest.js";
import { readStagedManifest, stagingDirFor } from "./staging.js";

export interface InvokeOptions {
  readonly story: string;
  readonly projectPath: string | null;
  readonly mode: "full" | "lean";
  readonly deepPlanning: boolean;
  readonly batchId: string;
  /** Hard cap so a runaway planner can't pin a worker forever. */
  readonly timeoutMs: number;
}

export type ProgressEvent = {
  readonly step: "planning" | "review" | "writing" | "info";
  readonly agent: string;
  readonly message: string;
};

export interface InvokeResult {
  readonly manifest: Manifest;
}

export type ProgressSink = (e: ProgressEvent) => void;

export type Invoker = (
  opts: InvokeOptions,
  onProgress: ProgressSink
) => Promise<InvokeResult>;

/**
 * Default invoker. Spawns `claude -p <prompt>` with the staging dir as
 * cwd. The prompt asks the /cards skill to plan the story and write
 * outputs into the staging dir.
 *
 * Stdout is line-buffered and each "[step] agent: message" prefix is
 * parsed into a ProgressEvent. Lines without a recognizable prefix get
 * sent up as `step: "info"`.
 *
 * The actual CLI flags + prompt template will evolve once the runner
 * design pass lands. This is the v1 wiring -- functional but expected
 * to be revisited.
 */
export const claudeCliInvoker: Invoker = async (opts, onProgress) => {
  const staging = stagingDirFor(opts.batchId);
  fs.mkdirSync(staging, { recursive: true });

  const cli = process.env["CLAUDE_CLI_PATH"] ?? "claude";
  const prompt = buildPrompt(opts, staging);

  return new Promise<InvokeResult>((resolve, reject) => {
    const child = spawn(cli, ["-p", prompt], {
      cwd: opts.projectPath ?? staging,
      env: {
        ...process.env,
        AGILE_CARDS_STAGING_DIR: staging,
        AGILE_CARDS_BATCH_ID: opts.batchId,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });

    const killTimer = setTimeout(() => {
      log.warn("claude invocation timeout", {
        batchId: opts.batchId,
        timeoutMs: opts.timeoutMs,
      });
      child.kill("SIGTERM");
    }, opts.timeoutMs);

    let stderrBuf = "";

    child.stdout.setEncoding("utf8");
    const lines = lineSplitter((line) => {
      const parsed = parseProgressLine(line);
      if (parsed) onProgress(parsed);
      else onProgress({ step: "info", agent: "cli", message: line });
    });
    child.stdout.on("data", (chunk: string) => lines(chunk));

    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk: string) => {
      stderrBuf += chunk;
    });

    child.on("error", (err) => {
      clearTimeout(killTimer);
      reject(new Error(`failed to spawn ${cli}: ${String(err)}`));
    });

    child.on("close", (code) => {
      clearTimeout(killTimer);
      lines.flush();
      if (code !== 0) {
        reject(
          new Error(
            `claude exited with code ${code}. stderr: ${stderrBuf.slice(0, 800)}`
          )
        );
        return;
      }
      try {
        const manifest = readStagedManifest(opts.batchId, {
          story: opts.story,
          projectPath: opts.projectPath,
          mode: opts.mode,
          deepPlanning: opts.deepPlanning,
        });
        if (manifest.cards.length === 0) {
          reject(
            new Error(
              "planner produced no cards. The story may be too small or the skill may have errored silently."
            )
          );
          return;
        }
        resolve({ manifest });
      } catch (err) {
        reject(new Error(`could not read staged manifest: ${String(err)}`));
      }
    });
  });
};

function buildPrompt(opts: InvokeOptions, stagingDir: string): string {
  const projectClause = opts.projectPath
    ? `--project "${opts.projectPath}"`
    : "(no project; treat as standalone story)";
  const modeClause =
    opts.mode === "lean" ? "--lean" : "(default full mode)";
  const deepClause = opts.deepPlanning ? "--deep" : "";

  return [
    `Invoke the /cards skill on the following story.`,
    ``,
    `Flags: ${projectClause} ${modeClause} ${deepClause}`.trim(),
    ``,
    `STAGING DIRECTIVE: write all card files and the manifest.json into`,
    `the staging dir below, NOT into the live backlog. The dashboard will`,
    `promote them to backlog after the human approves.`,
    `Staging dir: ${stagingDir}`,
    `Batch id: ${opts.batchId}`,
    ``,
    `Story:`,
    opts.story,
  ].join("\n");
}

/**
 * Parse a single stdout line into a ProgressEvent. We expect the CLI
 * (or a wrapper script the runner design pass adds) to emit lines like:
 *
 *   [planning] planner: decomposing story
 *   [review] reviewer: checking for hidden coupling
 *   [writing] cards: wrote b042-01.md
 *
 * Anything else is treated as info chatter.
 */
function parseProgressLine(line: string): ProgressEvent | null {
  const m = /^\[(planning|review|writing|info)]\s+([^:]+):\s*(.*)$/i.exec(
    line.trim()
  );
  if (!m) return null;
  const stepRaw = (m[1] ?? "").toLowerCase();
  const step =
    stepRaw === "planning" ||
    stepRaw === "review" ||
    stepRaw === "writing" ||
    stepRaw === "info"
      ? stepRaw
      : "info";
  return {
    step,
    agent: (m[2] ?? "claude").trim(),
    message: (m[3] ?? "").trim(),
  };
}

/**
 * Split incoming chunks on newlines and emit one full line at a time.
 * Returns a function with a `.flush()` method to drain any trailing
 * partial line on close.
 */
type LineSink = ((chunk: string) => void) & { flush: () => void };

function lineSplitter(onLine: (line: string) => void): LineSink {
  let buf = "";
  const push = (chunk: string): void => {
    buf += chunk;
    let idx = buf.indexOf("\n");
    while (idx !== -1) {
      const line = buf.slice(0, idx).replace(/\r$/, "");
      if (line.length > 0) onLine(line);
      buf = buf.slice(idx + 1);
      idx = buf.indexOf("\n");
    }
  };
  const flush = (): void => {
    if (buf.length > 0) {
      onLine(buf.replace(/\r$/, ""));
      buf = "";
    }
  };
  return Object.assign(push as (chunk: string) => void, { flush }) as LineSink;
}

/**
 * Generate a batch id. The /cards skill uses "b<NNN>" by walking
 * `_batches/.counter`, but the dashboard can't hold that lock from
 * outside. We use a timestamp + short random suffix that sorts well
 * lexicographically and won't collide with the skill's bNNN ids.
 */
export function generateBatchId(): string {
  const now = new Date();
  const ts =
    now.getFullYear().toString().slice(2) +
    pad2(now.getMonth() + 1) +
    pad2(now.getDate()) +
    pad2(now.getHours()) +
    pad2(now.getMinutes()) +
    pad2(now.getSeconds());
  const suffix = Math.random().toString(36).slice(2, 6);
  return `d${ts}-${suffix}`;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/**
 * Resolve the configured staging path for callers that want it without
 * importing the staging module directly. Re-exported here so the route
 * file only depends on the invoker module.
 */
export { stagingDirFor };
