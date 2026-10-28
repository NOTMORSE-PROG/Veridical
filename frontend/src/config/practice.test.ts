import { describe, expect, it } from "vitest";
import { practiceDestination } from "./practice";

const BASE = "https://practice.example.test/demo";
const ORIGIN = "https://account.example.test";

describe("public prepared-review destination", () => {
  it("retains source context on the configured origin", () => {
    expect(practiceDestination(BASE, ORIGIN, "/demo/report/1/document", "?page=2&redirect=https://elsewhere.test", "#source"))
      .toBe("https://practice.example.test/demo/report/1/document?page=2&redirect=https://elsewhere.test#source");
  });
  it.each(["", "invalid", "http://practice.example.test/demo", "https://u:p@practice.example.test/demo",
    `${BASE}?x=1`, `${BASE}#x`, "https://practice.example.test/other", `${ORIGIN}/demo`])
  ("refuses invalid or looping base %s", (base) => {
    expect(practiceDestination(base, ORIGIN)).toBeNull();
  });
  it.each(["//elsewhere.test/demo", "/signin", "/demo/../signin", "/demo/%2e%2e/signin",
    "/demo/%2f%2felsewhere.test", "/demo/%5csignin", "/demo/%252e%252e", "/demo/%invalid",
    "/demo/.\n./signin"])
  ("refuses unsupported or escaping path %s", (path) => {
    expect(practiceDestination(BASE, ORIGIN, path)).toBeNull();
  });
});
