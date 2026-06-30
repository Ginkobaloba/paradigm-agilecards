/**
 * CLI: revoke (delete) a bearer token by label.
 *
 *   npm run revoke-token -- --label "old-iphone"
 *
 * Idempotent. Exits 0 even if no token with that label existed; prints
 * the number of rows deleted so you can tell.
 */

import { revokeByLabel } from "../src/auth/tokens.js";

function parseArgs(argv: string[]): { label: string } {
  let label = "";
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--label" && i + 1 < argv.length) {
      const next = argv[i + 1];
      if (typeof next === "string") {
        label = next;
        i += 1;
      }
    }
  }
  if (!label) {
    process.stderr.write("Usage: npm run revoke-token -- --label <name>\n");
    process.exit(2);
  }
  return { label };
}

function main(): void {
  const { label } = parseArgs(process.argv.slice(2));
  const n = revokeByLabel(label);
  process.stdout.write(`revoked ${n} token(s) with label=${JSON.stringify(label)}\n`);
}

main();
