import { describe, expect, it } from "vitest";
import { REVIEW_DESK_RETURN as protocol } from "../config/signalRoutes";
import { readReviewDeskReturn, reviewDeskDestination, validDeskAction, validReviewDeskPath } from "./reviewDeskReturn";

const origin = "/dashboard?queue=ready_to_decide&status=checked&needs_review=false&q=Serbigo&program=IT&sort=oldest&page=3";

describe("Review Desk return boundary", () => {
  it.each(["https://evil.test/dashboard", "//evil.test/dashboard", "javascript:alert(1)", "/dashboard/../settings", "/dashboard/", "/dashboard#logout", "/dashboard?x=\n", "/dashboard?x=\u0085", "/dashboard\\evil", "/%64ashboard", "/settings", "/dashboard?" + "q".repeat(protocol.maxPathLength)])("rejects an unsafe or unsupported return target: %s", (target) => {
    expect(validReviewDeskPath(target)).toBeUndefined();
  });

  it("retains the exact internal origin query rather than inferring it from a report", () => {
    expect(validReviewDeskPath(origin)).toBe(origin);
    const to = reviewDeskDestination("/report/20/document?finding=38#evidence", { pathname: "/dashboard", search: origin.slice(origin.indexOf("?")) }, "review-desk-primary-16");
    const parsed = new URL(to, "https://example.test");
    expect(parsed.searchParams.get(protocol.pathParam)).toBe(origin);
    expect(parsed.searchParams.get(protocol.focusParam)).toBe("review-desk-primary-16");
    expect(parsed.searchParams.get("finding")).toBe("38");
    expect(parsed.hash).toBe("#evidence");
  });

  it("carries context through progress, report, source and evidence without replacing their queries", () => {
    let location = { pathname: "/dashboard", search: origin.slice(origin.indexOf("?")) };
    for (const target of ["/checks/20", "/report/20?flags_view=high", "/report/20/document?anchor=paragraph%204&finding=38", "/flags/38"]) {
      const url = new URL(reviewDeskDestination(target, location, "review-desk-prior-16"), "https://example.test");
      const context = readReviewDeskReturn({ pathname: url.pathname, search: url.search });
      expect(context).toEqual({ returnPath: origin, elementId: "review-desk-prior-16" });
      location = { pathname: url.pathname, search: url.search };
    }
  });

  it("keeps an already matching parent URL verbatim for exact focus restoration", () => {
    const params = new URLSearchParams({ desk_return: origin, desk_focus: "review-desk-primary-16" });
    const parent = `/report/20?${params}&flags_view=low&filter=literal%20space`;
    expect(reviewDeskDestination(parent, { pathname: "/flags/38", search: `?${params}` })).toBe(parent);
  });

  it.each(["/share/public-token", "/api/report/20", "/audit?check_run_id=20", "https://evil.test/report/20", "//evil.test/report/20"])("does not attach private list context to another surface: %s", (target) => {
    expect(reviewDeskDestination(target, { pathname: "/dashboard", search: "?q=private" })).toBe(target);
  });

  it("rejects ambiguous duplicate return parameters and ignores unsupported focus targets", () => {
    expect(readReviewDeskReturn({ pathname: "/flags/1", search: "?desk_return=%2Fdashboard&desk_return=%2Fsettings" })).toBeUndefined();
    expect(readReviewDeskReturn({ pathname: "/flags/1", search: "?desk_return=%2Fdashboard&desk_focus=logout" })).toEqual({ returnPath: "/dashboard", elementId: undefined });
    expect(validDeskAction("review-desk-primary-0")).toBeUndefined();
    expect(validDeskAction("review-desk-primary-16\n")).toBeUndefined();
    expect(validDeskAction("review-desk-primary-99999999999999999999")).toBeUndefined();
  });
});
