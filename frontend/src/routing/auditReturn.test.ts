import { describe, expect, it } from "vitest";
import { auditDestination, readAuditReviewOrigin, validAuditReviewOrigin } from "./auditReturn";

describe("same-review audit origin", () => {
  it("preserves the validated exact canonical, pane and desk context without private state", () => {
    const origin = "/report/21/document?pane=review&view=decision&criterion_record=35&desk_return=%2Fdashboard%3Fqueue%3Dneeds_review&desk_focus=review-desk-primary-21#criterion-record-21-35";
    const context = validAuditReviewOrigin(origin, 21)!;
    expect(context.returnPath).toBe(origin);
    expect(context.focusPath).toBe(origin.split("#")[0]);
    expect(context.elementId).toBe("criterion-record-21-35");
    expect(context.fallbackElementId).toBe("report-audit-21");
    expect(context.scrollBlock).toBe("start");
    const hashAt = origin.indexOf("#"), queryAt = origin.indexOf("?");
    const destination = auditDestination(21, { pathname: origin.slice(0, queryAt), search: origin.slice(queryAt, hashAt), hash: origin.slice(hashAt) });
    expect(readAuditReviewOrigin(destination.slice(destination.indexOf("?")), 21)).toEqual(context);
    expect(destination).not.toMatch(/reason|draft|note/);
  });

  it("returns to an explicit reading section while a no-hash origin keeps its Audit action", () => {
    expect(validAuditReviewOrigin("/report/21?criterion_record=all#criteria-results-heading", 21)).toMatchObject({ elementId: "criteria-results-heading", fallbackElementId: "report-audit-21" });
    expect(validAuditReviewOrigin("/report/21?criterion_record=35", 21)).toMatchObject({ elementId: "report-audit-21", fallbackElementId: "readiness-report-heading" });
    expect(validAuditReviewOrigin("/report/21?criterion_record=35", 21)?.scrollBlock).toBeUndefined();
  });

  it.each([
    "https://evil.test/report/21", "//evil.test/report/21", "/report/21\\outside", "/report/21/../22",
    "/report/%32%31", "/report/22", "/report/21?criterion_record=35&criterion_record=36",
    "/report/21?reason=private", "/report/21?draft=secret", "/report/21?return=https%3A%2F%2Fevil.test",
    "/report/21?__proto__=x", "/report/21?pane=review%0A", "/report/21?finding=0",
    "/report/21?criterion_record=35#criterion-record-22-35", "/report/21?criterion_record=35#criterion-record-21-36",
    "/report/21?criterion_record=all#final-decision", "/report/21#criterion-record-21-35",
    "/report/21?desk_focus=review-desk-primary-21", "/report/21?desk_return=%2F%2Fevil.test",
    "/report/21#https://evil.test", "/report/21#%63riterion-record-21-35",
  ])("rejects unsafe, contradictory or private origin %s", origin => {
    expect(validAuditReviewOrigin(origin, 21)).toBeUndefined();
  });

  it("does not trust duplicate or contradictory outer run/origin identities", () => {
    expect(readAuditReviewOrigin("?check_run_id=21&review_return=%2Freport%2F21&review_return=%2Freport%2F22", 21)).toBeUndefined();
    expect(readAuditReviewOrigin("?check_run_id=21&check_run_id=22&review_return=%2Freport%2F21", 21)).toBeUndefined();
    expect(readAuditReviewOrigin("?check_run_id=22&review_return=%2Freport%2F21", 21)).toBeUndefined();
    expect(readAuditReviewOrigin("?check_run_id=21", 21)).toBeUndefined();
  });

  it("retains actual severity filters and opened finding identifiers", () => {
    const path = "/report/21?flags_view=med&flags_clusters_open=39%2C40&flags_visible=24#integrity-signals";
    expect(validAuditReviewOrigin(path, 21)?.returnPath).toBe(path);
    expect(validAuditReviewOrigin(path.replace("39%2C40", "39%2C39"), 21)).toBeUndefined();
    expect(validAuditReviewOrigin(path.replace("39%2C40", "39%2Cnope"), 21)).toBeUndefined();
  });

  it.each([
    "reason=FAKE-SECRET", "draft=FAKE-SECRET", "note=FAKE-SECRET", "unknown=value", "rerun=21",
    "queue=needs_review&queue=complete", "queue=complete&status=checking", "queue=complete&needs_review=true",
    "desk_return=%2Fdashboard", "page=0", "page=9007199254740992", "q=bad%0Avalue",
  ])("rejects arbitrary or contradictory nested desk query %s", query => {
    const nested = encodeURIComponent(`/dashboard?${query}`);
    expect(validAuditReviewOrigin(`/report/21?desk_return=${nested}`, 21)).toBeUndefined();
    expect(auditDestination(21, { pathname: "/report/21", search: `?desk_return=${nested}`, hash: "" })).toBe("/audit?check_run_id=21");
  });

  it("retains genuine nested queue, legacy mapping, search and paging bytes", () => {
    const nested = encodeURIComponent("/dashboard?queue=ready_to_decide&status=checked&needs_review=false&sort=oldest&page=2&q=two+words&program=BSIT");
    const path = `/report/21?desk_return=${nested}&desk_focus=review-desk-primary-21`;
    expect(validAuditReviewOrigin(path, 21)?.returnPath).toBe(path);
  });
});
