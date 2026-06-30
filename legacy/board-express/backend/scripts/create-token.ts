/**
 * CLI: create a new bearer token. Prints the plaintext exactly once and
 * stores only the SHA-256 hash. Usage:
 *
 *   npm run create-token -- --label "drew-laptop"
 *
 * The label is required and free-form; it's what shows up in
 * `list-tokens` so you can tell which device a token belongs to.
 */

import { mintToken } from "../src/auth/tokens.js";

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
    process.stderr.write(
      "Usage: npm run create-token -- --label \"some-device-name\"\n"
    );
    process.exit(2);
  }
  return { label };
}

function main(): void {
  const { label } = parseArgs(process.argv.slice(2));
  const minted = mintToken(label);
  process.stdout.write(
    [
      "",
      "Token created. Copy this and save it; it won't be shown again.",
      "",
      `  label:     ${minted.label}`,
      `  token:     ${minted.plaintext}`,
      "",
      "Use it in the dashboard's login screen, or pass it as",
      "  Authorization: Bearer <token>",
      "",
    ].join("\n")
  );
}

main();
