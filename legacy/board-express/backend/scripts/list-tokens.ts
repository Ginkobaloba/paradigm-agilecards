/**
 * CLI: list all bearer tokens (label + timestamps; never plaintext).
 *
 *   npm run list-tokens
 */

import { listTokens } from "../src/auth/tokens.js";

function main(): void {
  const rows = listTokens();
  if (rows.length === 0) {
    process.stdout.write("No tokens. Create one with `npm run create-token -- --label <name>`.\n");
    return;
  }
  const w = (s: string, n: number): string => s.padEnd(n, " ").slice(0, n);
  process.stdout.write(
    [
      `${w("id", 4)} ${w("label", 24)} ${w("created", 22)} ${w("last_used", 22)}`,
      "-".repeat(72),
      ...rows.map(
        (r) =>
          `${w(String(r.id), 4)} ${w(r.label, 24)} ${w(r.createdAt, 22)} ${w(r.lastUsedAt ?? "never", 22)}`
      ),
      "",
    ].join("\n")
  );
}

main();
