/**
 * SHA-256 helper. Tokens are stored as hashes; comparison is constant-time.
 * Tokens themselves are 32 random bytes encoded as base64url, so they have
 * plenty of entropy for a private dashboard.
 */

import crypto from "node:crypto";

export function sha256Hex(input: string): string {
  return crypto.createHash("sha256").update(input, "utf8").digest("hex");
}

export function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(Buffer.from(a, "hex"), Buffer.from(b, "hex"));
}

export function generateTokenPlaintext(): string {
  return crypto.randomBytes(32).toString("base64url");
}
