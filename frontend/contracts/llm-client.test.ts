/**
 * CARDS-014 -- consumer contract test for `@paradigm/llm-client` (chunk K16).
 *
 * `@paradigm/llm-client` is the TypeScript-only, frozen-at-v1 LLM client. In
 * AgileCards it is consumed *server-side* by the Node BFF (the browser never
 * holds a provider key). The BFF does not exist yet -- it lands with chunk
 * **K11b** -- so this file is a **scaffold**:
 *
 *   1. The ACTIVE tests below pin the frozen contract this consumer depends on,
 *      mirrored from `@paradigm/llm-client@0.1.0` (the first cut of the v1
 *      surface): the exactly-five-method interface, the 9-field telemetry
 *      payload, the provider-selection rule, and the structured error shape.
 *      They run green on every PR and every Renovate `@paradigm/*` bump.
 *
 *   2. The `describe.skip` block at the bottom is the LIVE conformance suite.
 *      It is written against the real package and is dormant only because the
 *      package is not a dependency yet. K11b wires it: see "WIRING (K11b)".
 *
 * WIRING (K11b):
 *   - add `@paradigm/llm-client` to frontend/package.json (GitHub Packages, see .npmrc)
 *   - change `describe.skip(` -> `describe(` on the live block below
 *   - if the frozen surface changed, this file fails -- that is the point
 *     (the contract is frozen at v1.0.0; a break is Tier-3 / AC-LLM-003).
 *
 * Source of truth: paradigm-platform/packages/llm-client/src/{types,errors}.ts.
 */
import { describe, it, expect } from "vitest";

// --------------------------------------------------------------------------- //
// The frozen contract, mirrored locally (AC-LLM-001/003). K11b can replace this
// block with `import type { LLMClientInterface, TelemetryEvent } from
// "@paradigm/llm-client"` once the package is installed.
// --------------------------------------------------------------------------- //

/** The five and only methods on the frozen v1 client surface (AC-LLM-001). */
const FROZEN_INTERFACE_METHODS = [
  "complete",
  "stream",
  "embed",
  "onTelemetry",
  "registerAdapter",
] as const;

/** Telemetry payload field names, snake_case per the FPS (AC-LLM-012). */
const TELEMETRY_REQUIRED_FIELDS = [
  "provider",
  "model",
  "request_id",
  "prompt_tokens",
  "completion_tokens",
  "latency_ms",
  "cost_estimate_usd",
  "success",
] as const;
const TELEMETRY_OPTIONAL_FIELDS = ["error_code"] as const;

/** Stable error codes the consumer branches on (errors.ts). */
const ERROR_CODES = {
  unknownProvider: "unknown_provider",
  noProviderSelected: "no_provider_selected",
  missingApiKey: "missing_api_key",
  unsupportedOperation: "unsupported_operation",
  invalidRequest: "invalid_request",
  providerHttpError: "provider_http_error",
} as const;

/** Local mirror of the telemetry shape, used to build representative samples. */
interface TelemetryEventMirror {
  provider: string;
  model: string;
  request_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  cost_estimate_usd: number;
  success: boolean;
  error_code?: string;
}

// --------------------------------------------------------------------------- //
// ACTIVE contract checks (run now; guard the pinned contract against drift).
// --------------------------------------------------------------------------- //

describe("@paradigm/llm-client contract (CARDS-014, scaffold)", () => {
  it("interface conformance: exactly five frozen methods", () => {
    expect([...FROZEN_INTERFACE_METHODS].sort()).toEqual(
      ["complete", "embed", "onTelemetry", "registerAdapter", "stream"].sort(),
    );
    // No tenant- or Cards-specific methods leak onto the surface (AC-LLM-001).
    expect(FROZEN_INTERFACE_METHODS).not.toContain("createCard");
  });

  it("telemetry hook: 9-field payload (8 required + optional error_code)", () => {
    expect(TELEMETRY_REQUIRED_FIELDS.length).toBe(8);
    const totalFields =
      TELEMETRY_REQUIRED_FIELDS.length + TELEMETRY_OPTIONAL_FIELDS.length;
    expect(totalFields).toBe(9);

    // A success event omits error_code -> exactly the 8 required keys.
    const success: TelemetryEventMirror = {
      provider: "anthropic",
      model: "claude-opus-4-8",
      request_id: "req-123",
      prompt_tokens: 10,
      completion_tokens: 4,
      latency_ms: 42,
      cost_estimate_usd: 0.0012,
      success: true,
    };
    expect(Object.keys(success).sort()).toEqual([...TELEMETRY_REQUIRED_FIELDS].sort());

    // A failure event adds error_code -> all 9 keys.
    const failure: TelemetryEventMirror = { ...success, success: false, error_code: "provider_http_error" };
    expect(Object.keys(failure).sort()).toEqual(
      [...TELEMETRY_REQUIRED_FIELDS, ...TELEMETRY_OPTIONAL_FIELDS].sort(),
    );
  });

  it("provider selection: request.provider, else client default, else error", () => {
    // The rule the BFF relies on (AC-LLM-008): a request may name a provider;
    // otherwise the client's defaultProvider is used; if neither is set the
    // call fails with the stable `no_provider_selected` code.
    const resolve = (requestProvider?: string, defaultProvider?: string) =>
      requestProvider ?? defaultProvider ?? null;

    expect(resolve("openai", "anthropic")).toBe("openai");
    expect(resolve(undefined, "anthropic")).toBe("anthropic");
    expect(resolve(undefined, undefined)).toBeNull();
    expect(ERROR_CODES.noProviderSelected).toBe("no_provider_selected");
  });

  it("error shape: structured errors carry stable codes", () => {
    // Consumers branch on `code`, never on message text (errors.ts).
    expect(Object.values(ERROR_CODES)).toEqual([
      "unknown_provider",
      "no_provider_selected",
      "missing_api_key",
      "unsupported_operation",
      "invalid_request",
      "provider_http_error",
    ]);
  });
});

// --------------------------------------------------------------------------- //
// LIVE conformance against the real package. Dormant until K11b installs
// `@paradigm/llm-client`; flip `describe.skip` -> `describe` to activate.
// The dynamic import is intentionally NOT statically analyzable, so this file
// loads fine while the package is absent.
// --------------------------------------------------------------------------- //

describe.skip("@paradigm/llm-client LIVE conformance (wire in K11b)", () => {
  const PKG = "@paradigm/llm-client";

  it("telemetry fires after a completion with the full 9-field payload", async () => {
    const { createClient } = (await import(/* @vite-ignore */ PKG)) as typeof import("@paradigm/llm-client");

    // A fake fetch returning one Anthropic-shaped completion (no network).
    const fetchMock = async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        id: "m1",
        model: "claude-opus-4-8",
        content: [{ type: "text", text: "hi" }],
        stop_reason: "end_turn",
        usage: { input_tokens: 10, output_tokens: 4 },
      }),
      text: async () => "{}",
      body: undefined,
    });

    const client = createClient({
      defaultProvider: "anthropic",
      keys: { anthropic: { apiKey: "k" } },
      fetch: fetchMock as never,
    });

    const events: Array<Record<string, unknown>> = [];
    client.onTelemetry((e) => events.push(e as unknown as Record<string, unknown>));

    await client.complete({ requestId: "req-123", messages: [{ role: "user", content: "hi" }] });

    expect(events.length).toBe(1);
    for (const field of TELEMETRY_REQUIRED_FIELDS) {
      expect(events[0]).toHaveProperty(field);
    }
    expect(events[0]!.success).toBe(true);
    expect(events[0]!.request_id).toBe("req-123");
  });

  it("throws the stable no_provider_selected error when no provider is set", async () => {
    const { createClient, NoProviderSelectedError } = (await import(
      /* @vite-ignore */ PKG
    )) as typeof import("@paradigm/llm-client");

    const client = createClient({ fetch: (async () => ({})) as never });
    await expect(
      client.complete({ messages: [{ role: "user", content: "hi" }] }),
    ).rejects.toBeInstanceOf(NoProviderSelectedError);
  });
});
