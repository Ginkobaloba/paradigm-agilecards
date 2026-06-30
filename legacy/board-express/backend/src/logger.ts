/**
 * Tiny structured logger. We don't pull in pino for a project this size,
 * but the API mirrors pino so we can swap later without touching call
 * sites.
 */

import { config } from "./config.js";

type Level = "error" | "warn" | "info" | "debug";

const ORDER: Record<Level, number> = { error: 0, warn: 1, info: 2, debug: 3 };

function shouldLog(level: Level): boolean {
  return ORDER[level] <= ORDER[config.logLevel];
}

function emit(level: Level, msg: string, fields?: Record<string, unknown>): void {
  if (!shouldLog(level)) return;
  const line = {
    t: new Date().toISOString(),
    level,
    msg,
    ...(fields ?? {}),
  };
  const stream = level === "error" || level === "warn" ? process.stderr : process.stdout;
  stream.write(JSON.stringify(line) + "\n");
}

export const log = {
  error: (msg: string, fields?: Record<string, unknown>): void => emit("error", msg, fields),
  warn: (msg: string, fields?: Record<string, unknown>): void => emit("warn", msg, fields),
  info: (msg: string, fields?: Record<string, unknown>): void => emit("info", msg, fields),
  debug: (msg: string, fields?: Record<string, unknown>): void => emit("debug", msg, fields),
};
