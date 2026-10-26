/// <reference types="node" />
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { createHash, webcrypto } from "node:crypto";
import { resolve } from "node:path";
import { installWorkspaceSample } from "./workspaceApi";
import { companionSource, reviewTasks, sampleCriteria, sampleFlags, sampleReport, WORKSPACE_SAMPLE } from "./workspaceFixture";

const originalFetch = window.fetch;
afterEach(() => { window.fetch = originalFetch; sessionStorage.clear(); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function start() { sessionStorage.clear(); localStorage.clear(); const network = vi.fn<typeof fetch>(async () => { throw new Error("Unexpected live request"); }); window.fetch = network; installWorkspaceSample(); return network; }
const call = async (path: string, method = "GET", body?: unknown) => { const response = await window.fetch(`/api${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) }); return { response, data: await response.json() }; };
const resolveAll = async (reason = "Instructor judgment after reading the original pages.") => {
  for (const criterion of sampleCriteria) await call(`/check-runs/1/escalated/${100 + criterion.id}/resolve`, "POST", { resolution: "mark_level", level: 3, reason });
};

describe("real-interface sample API boundary", () => {
  it.each([undefined, "", "   ", " OK "])("saves an ordinary grade with optional comment %s and preserves it on reload", async reason => {
    const network = start();
    const saved = await call("/check-runs/1/escalated/101/resolve", "POST", { resolution: "mark_level", level: 3, ...(reason === undefined ? {} : { reason }) });
    expect(saved.response.status).toBe(200);
    expect(saved.data.report.pending_review_count).toBe(3);
    expect(saved.data.report.results[0].resolution).toMatchObject({ type: "mark_level", reason: reason?.trim() ?? "" });
    window.fetch = network;
    installWorkspaceSample();
    const report = (await call("/check-runs/1/report")).data;
    expect(report.results[0].resolution.reason).toBe(reason?.trim() ?? "");
    expect(report.results[0].level.ordinal).toBe(3);
    expect((await call("/check-runs/1/escalated")).data).toHaveLength(3);
    const share = (await call("/check-runs/1/share", "POST", { expires_at: null })).data;
    const publicRow = (await call(`/shared/${share.token}/report`)).data.report.results[0];
    expect(publicRow.resolved).toBe(true);
    expect(publicRow).not.toHaveProperty("resolution");
  });

  it.each([undefined, "", "   ", "OK"])("still requires a substantive exclusion reason: %s", async reason => {
    start();
    expect((await call("/check-runs/1/escalated/101/resolve", "POST", { resolution: "needs_document", reason })).response.status).toBe(422);
    expect((await call("/check-runs/1/report")).data.pending_review_count).toBe(4);
    expect((await call("/check-runs/1/report")).data.results[0].resolution).toBeNull();
  });
  it("separately allowlists shared images and denies wrong IDs, selectors, corrupted bytes and revoked access", async () => {
    vi.stubGlobal("crypto", webcrypto);
    const network = start();
    network.mockImplementation(async input => new Response(readFileSync(resolve(`public${String(input)}`))));
    const share = (await call("/check-runs/1/share", "POST", { expires_at: null })).data;
    const shared = (await call(`/shared/${share.token}/report`)).data;
    expect(shared.evidence_images).toHaveLength(18);
    const image = shared.evidence_images[0];
    const imagePath = `/shared/${share.token}/evidence-images/${image.image_id}`;
    const response = await window.fetch(`/api${imagePath}`);
    expect(response.status).toBe(200);
    expect(createHash("sha256").update(new Uint8Array(await response.arrayBuffer())).digest("hex")).toBe(image.image_sha256);
    expect((await call(`${imagePath}?page=202`)).response.status).toBe(404);
    expect((await call(`/shared/${share.token}/evidence-images/e-000000000000000000000000`)).response.status).toBe(404);
    network.mockImplementation(async () => new Response("corrupted image"));
    expect((await call(imagePath)).response.status).toBe(422);
    await call("/check-runs/1/share", "DELETE");
    expect((await call(imagePath)).response.status).toBe(410);
  });

  it("rejects malformed expiry and prevents unknown future private fields entering a share", async () => {
    const network = start();
    expect((await call("/check-runs/1/share", "POST", { expires_at: "not-a-date" })).response.status).toBe(422);
    const saved = JSON.parse(sessionStorage.getItem(WORKSPACE_SAMPLE.storageKey)!);
    saved.state.report.future_private = "PRIVATE-FUTURE-REPORT";
    saved.state.report.results[0].future_private = "PRIVATE-FUTURE-CRITERION";
    saved.state.flags[0].annotation = "PRIVATE-ANNOTATION";
    sessionStorage.setItem(WORKSPACE_SAMPLE.storageKey, JSON.stringify(saved));
    window.fetch = network;
    installWorkspaceSample();
    const share = (await call("/check-runs/1/share", "POST", { expires_at: null })).data;
    const shared = await call(`/shared/${share.token}/report`);
    expect(JSON.stringify(shared.data)).not.toContain("PRIVATE-");
    expect(shared.data.flags[0].problem_kind).toBe("implementation_scope_mismatch");
    expect((await call("/check-runs/1/share", "POST", { expires_at: 123 })).response.status).toBe(422);
    expect((await call(`/shared/${share.token}/report`)).response.status).toBe(200);
  });
  it("prepares real source assets, exposes running progress and retains saved judgments", async () => {
    sessionStorage.clear(); localStorage.clear();
    vi.stubGlobal("crypto", webcrypto);
    let releaseOriginal!: () => void;
    const sourceGate = new Promise<void>(resolveGate => { releaseOriginal = resolveGate; });
    const network = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith(".pdf")) await sourceGate;
      return new Response(readFileSync(resolve(`public${String(input)}`)));
    });
    window.fetch = network;
    installWorkspaceSample();
    await call("/check-runs/1/escalated/101/resolve", "POST", { resolution: "mark_level", level: 2, reason: "Scope distinctions require substantial clarification." });
    expect((await call("/check-runs", "POST", { manuscript_id: 1, rubric_id: 1 })).data.status).toBe("ingesting");
    expect((await call("/stats")).data.checking_count).toBe(1);
    expect((await call("/manuscripts?queue=checking")).data.items).toHaveLength(1);
    expect((await call("/manuscripts?queue=needs_review")).data.items).toHaveLength(0);
    releaseOriginal();
    await vi.waitFor(async () => expect((await call("/check-runs/1")).data.status).toBe("done"));
    expect((await call("/check-runs/1/report")).data.pending_review_count).toBe(3);
    expect((await call("/stats")).data.checking_count).toBe(0);
    expect((await call("/check-runs/1")).data.stage_status.stages.semantic.status).toBe("skipped");
    expect(network).toHaveBeenCalledTimes(2);
  });

  it("cancels source loading and ignores a late original response", async () => {
    sessionStorage.clear(); localStorage.clear();
    let releaseOriginal!: () => void;
    const gate = new Promise<void>(resolveGate => { releaseOriginal = resolveGate; });
    window.fetch = vi.fn(async () => { await gate; return new Response("late response"); });
    installWorkspaceSample();
    await call("/check-runs", "POST", { manuscript_id: 1, rubric_id: 1 });
    expect((await call("/check-runs/1/cancel", "POST", {})).data.status).toBe("cancelled");
    releaseOriginal();
    await vi.waitFor(async () => expect((await call("/check-runs/1")).data.status).toBe("cancelled"));
    expect((await call("/check-runs/1/report")).data.pending_review_count).toBe(4);
  });
  it("preserves the complete licensed source and binds excerpts to original pages", () => {
    const bytes = readFileSync(resolve("public/sample-assets/stock-inventory-capstone.pdf"));
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(WORKSPACE_SAMPLE.sha256);
    expect(bytes.subarray(0, 5).toString()).toBe("%PDF-");
    const pages = JSON.parse(readFileSync(resolve("public/sample-assets/manuscript-text.json"), "utf8")) as { page: number; text: string }[];
    expect(pages).toHaveLength(109);
    for (const flag of sampleFlags) { const page = Number(flag.page_anchor.match(/\d+/)![0]); expect(pages[page - 1].text.replace(/\s+/g, " ")).toContain(flag.evidence_excerpt.replace(/\s+/g, " ")); }
    for (const task of reviewTasks) expect(pages[task.page - 1].text.replace(/\s+/g, " ")).toContain(task.quote.replace(/\s+/g, " "));
    const companionBytes = readFileSync(resolve("public/sample-assets/e-aimss.pdf"));
    expect(createHash("sha256").update(companionBytes).digest("hex")).toBe(companionSource.sha256);
    expect(companionSource.sha256).not.toBe(WORKSPACE_SAMPLE.sha256);
    expect(sampleFlags.some(flag => flag.check_kind === "originality_reuse")).toBe(false);
  });

  it("serves actual application DTOs without calling a live backend and denies unknown operations", async () => {
    const network = start();
    expect((await call("/auth/session")).data.instructor.display_name).toBe("Capstone Instructor");
    expect((await call("/manuscripts?queue=needs_review")).data.items).toHaveLength(1);
    expect((await call("/check-runs/1/report")).data.llm_mode).toBe("manual");
    expect((await call("/check-runs/1/report")).data.results.every((row: { score: number | null; level: unknown }) => row.score === null && row.level === null)).toBe(true);
    expect((await call("/check-runs/1/escalated")).data).toHaveLength(4);
    expect((await call("/library/202/document")).data).toMatchObject({ page_count: 19, original_filename: companionSource.filename, regions: [] });
    const unknown = await call("/never-forward-to-production", "POST", { action: "delete" });
    expect(unknown.response.status).toBe(422);
    expect(network).not.toHaveBeenCalled();
  });

  it("records reasoned judgments, instructor decisions, reopening, and a retained audit trail", async () => {
    start();
    expect((await call("/check-runs/1/decision", "POST", { decision: "approved" })).response.status).toBe(409);
    expect((await call("/check-runs/1/escalated/102/resolve", "POST", { resolution: "mark_level", level: 3, reason: "The report distinguishes its implementation limitations." })).data.report.pending_review_count).toBe(3);
    expect((await call("/check-runs/1/decision", "POST", { decision: "approved" })).response.status).toBe(409);
    await resolveAll();
    expect((await call("/check-runs/1/report")).data).toMatchObject({ pending_review_count: 0, composite_score: 75, status: "conditionally_ready" });
    expect((await call("/check-runs/1/decision", "POST", { decision: "returned", note: "Clarify planned versus completed work." })).data.decision).toBe("returned");
    expect((await call("/check-runs/1/reopen", "POST", { reason: "Review the revision." })).data.decision).toBeNull();
    expect((await call("/audit")).data.items.some((event: { event_type: string }) => event.event_type === "decision_recorded")).toBe(true);
  });

  it("shares a same-browser read-only snapshot without private reasons and revokes it", async () => {
    start();
    await resolveAll("PRIVATE-CRITERION-REASON");
    await call("/flags/1/annotate", "POST", { annotation: "PRIVATE-ANNOTATION" });
    const share = (await call("/check-runs/1/share", "POST", { expires_at: null })).data;
    const shared = await call(`/shared/${share.token}/report`);
    expect(shared.response.status).toBe(200);
    expect(JSON.stringify(shared.data)).not.toContain("PRIVATE-CRITERION-REASON");
    expect(JSON.stringify(shared.data)).not.toContain("PRIVATE-ANNOTATION");
    await call("/check-runs/1/decision", "POST", { decision: "returned", note: "PUBLIC-DECISION-NOTE" });
    expect((await call(`/shared/${share.token}/report`)).data.report.decision_note).toBe("PUBLIC-DECISION-NOTE");
    await call("/check-runs/1/share", "DELETE");
    expect((await call(`/shared/${share.token}/report`)).response.status).toBe(410);
  });

  it("rejects an invalid rubric level without grading the criterion", async () => {
    start();
    expect((await call("/check-runs/1/escalated/101/resolve", "POST", { resolution: "mark_level", level: 99, reason: "A judgment needs a valid course level." })).response.status).toBe(422);
    expect((await call("/check-runs/1/report")).data).toMatchObject({ pending_review_count: 4, composite_score: null });
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
