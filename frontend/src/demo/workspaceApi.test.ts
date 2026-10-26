/// <reference types="node" />
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { installWorkspaceSample } from "./workspaceApi";
import { sampleFlags, sampleReport, WORKSPACE_SAMPLE } from "./workspaceFixture";

const originalFetch = window.fetch;
afterEach(() => { window.fetch = originalFetch; sessionStorage.clear(); localStorage.clear(); vi.restoreAllMocks(); });
function start() { sessionStorage.clear(); localStorage.clear(); const network = vi.fn(() => Promise.reject(new Error("Unexpected live request"))); window.fetch = network; installWorkspaceSample(); return network; }
const call = async (path: string, method = "GET", body?: unknown) => { const response = await window.fetch(`/api${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) }); return { response, data: await response.json() }; };

describe("real-interface sample API boundary", () => {
  it("preserves the complete licensed source and binds excerpts to original pages", () => {
    const bytes = readFileSync(resolve("public/sample-assets/stock-inventory-capstone.pdf"));
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(WORKSPACE_SAMPLE.sha256);
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
    const pages = JSON.parse(readFileSync(resolve("public/sample-assets/manuscript-text.json"), "utf8")) as { page: number; text: string }[];
    expect(pages).toHaveLength(109);
    for (const flag of sampleFlags) { const page = Number(flag.page_anchor.match(/\d+/)![0]); expect(pages[page - 1].text.replace(/\s+/g, " ")).toContain(flag.evidence_excerpt.replace(/\s+/g, " ")); }
  });

  it("serves actual application DTOs without calling a live backend and denies unknown operations", async () => {
    const network = start();
    expect((await call("/auth/session")).data.instructor.display_name).toBe("Sample Instructor");
    expect((await call("/manuscripts?queue=needs_review")).data.items).toHaveLength(1);
    expect((await call("/check-runs/1/report")).data.llm_mode).toBe("fake");
    expect((await call("/check-runs/1/escalated")).data).toHaveLength(1);
    const unknown = await call("/never-forward-to-production", "POST", { action: "delete" });
    expect(unknown.response.status).toBe(422);
    expect(network).not.toHaveBeenCalled();
  });

  it("records reasoned judgments, instructor decisions, reopening, and a retained audit trail", async () => {
    start();
    expect((await call("/check-runs/1/decision", "POST", { decision: "approved" })).response.status).toBe(409);
    expect((await call("/check-runs/1/escalated/102/resolve", "POST", { resolution: "mark_level", level: 3, reason: "The report distinguishes its implementation limitations." })).data.report.pending_review_count).toBe(0);
    expect((await call("/check-runs/1/decision", "POST", { decision: "returned", note: "Clarify planned versus completed work." })).data.decision).toBe("returned");
    expect((await call("/check-runs/1/reopen", "POST", { reason: "Review the revision." })).data.decision).toBeNull();
    expect((await call("/audit")).data.items.some((event: { event_type: string }) => event.event_type === "decision_recorded")).toBe(true);
  });

  it("shares a same-browser read-only snapshot without private reasons and revokes it", async () => {
    start();
    await call("/check-runs/1/escalated/102/resolve", "POST", { resolution: "mark_pass", reason: "PRIVATE-CRITERION-REASON" });
    await call("/flags/1/annotate", "POST", { annotation: "PRIVATE-ANNOTATION" });
    const share = (await call("/check-runs/1/share", "POST", { expires_at: null })).data;
    const shared = await call(`/shared/${share.token}/report`);
    expect(shared.response.status).toBe(200);
    expect(JSON.stringify(shared.data)).not.toContain("PRIVATE-CRITERION-REASON");
    expect(JSON.stringify(shared.data)).not.toContain("PRIVATE-ANNOTATION");
    await call("/check-runs/1/share", "DELETE");
    expect((await call(`/shared/${share.token}/report`)).response.status).toBe(410);
  });

  it("leaves the prepared result independent of edits to the format", async () => {
    start();
    const original = structuredClone(sampleReport.results);
    const rubric = (await call("/rubrics/1")).data;
    rubric.criteria[0].text = "A changed practice criterion";
    await call("/rubrics/1/criteria", "PUT", { criteria: rubric.criteria, confirm: true });
    expect((await call("/check-runs/1/report")).data.results).toEqual(original);
    expect((await call("/rubrics/1")).data.criteria[0].text).toBe("A changed practice criterion");
  });
});
