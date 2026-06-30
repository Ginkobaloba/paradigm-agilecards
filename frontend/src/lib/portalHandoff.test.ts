import { describe, expect, it } from "vitest";

import { parsePortalToken, stripPortalToken } from "./portalHandoff";

describe("parsePortalToken", () => {
  it("returns null for an empty hash", () => {
    expect(parsePortalToken("")).toBeNull();
    expect(parsePortalToken("#")).toBeNull();
  });

  it("extracts the token with a leading #", () => {
    expect(parsePortalToken("#portal_token=abc.def.ghi")).toBe("abc.def.ghi");
  });

  it("extracts the token without a leading #", () => {
    expect(parsePortalToken("portal_token=abc.def.ghi")).toBe("abc.def.ghi");
  });

  it("ignores other fragment params", () => {
    expect(parsePortalToken("#foo=1&portal_token=jwt&bar=2")).toBe("jwt");
  });

  it("returns null when the key is absent", () => {
    expect(parsePortalToken("#state=xyz")).toBeNull();
  });

  it("returns null for an empty token value", () => {
    expect(parsePortalToken("#portal_token=")).toBeNull();
  });
});

describe("stripPortalToken", () => {
  it("removes the token and leaves nothing else", () => {
    expect(stripPortalToken("#portal_token=jwt")).toBe("");
  });

  it("preserves other fragment params", () => {
    expect(stripPortalToken("#foo=1&portal_token=jwt&bar=2")).toBe(
      "foo=1&bar=2",
    );
  });

  it("is a no-op when the token is absent", () => {
    expect(stripPortalToken("#state=xyz")).toBe("state=xyz");
  });
});
