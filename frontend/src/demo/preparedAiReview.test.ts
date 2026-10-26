import { afterEach, describe, expect, it, vi } from "vitest";
import { installWorkspaceSample } from "./workspaceApi";
import { AI_REVIEW_BASIS, AI_REVIEW_CONFIRMED_BASIS, AI_REVIEW_VERSION, applyPreparedAiReview } from "./preparedAiReview";
import { sampleFlags, sampleLibrary, sampleManuscript, sampleReport, sampleRubric, sampleRun, WORKSPACE_SAMPLE } from "./workspaceFixture";

const originalFetch = window.fetch;
const fresh = () => ({ run: structuredClone(sampleRun), report: structuredClone(sampleReport), flags: structuredClone(sampleFlags), rubric: structuredClone(sampleRubric), manuscript: structuredClone(sampleManuscript), library: structuredClone(sampleLibrary), share: null, fileAvailable: true, events: [], aiReviewVersion: undefined as string | undefined });
const store = (state: ReturnType<typeof fresh>) => sessionStorage.setItem(WORKSPACE_SAMPLE.storageKey, JSON.stringify({ version: 1, state }));
const call = async (path: string, body?: unknown) => { const r = await fetch(`/api${path}`, { method: body ? "POST" : "GET", ...(body ? { body: JSON.stringify(body) } : {}) }); return { status: r.status, data: await r.json() }; };
afterEach(() => { window.fetch = originalFetch; sessionStorage.clear(); localStorage.clear(); vi.restoreAllMocks(); });

describe("completed AI-review case", () => {
  it("loads four actual session grades, calculates readiness and keeps the teacher decision open", async () => {
    const network = vi.fn(); window.fetch = network; installWorkspaceSample();
    const report = (await call("/check-runs/1/report")).data;
    expect(report).toMatchObject({ pending_review_count: 0, status: "conditionally_ready", composite_score: 60, decision: null, llm_mode: "manual" });
    expect(report.results.map((row: { level: { name: string } }) => row.level.name)).toEqual(["Developing", "Developing", "Sufficient", "Sufficient"]);
    expect(report.results.every((row: { basis: string; resolution: unknown }) => row.basis === AI_REVIEW_BASIS && row.resolution === null)).toBe(true);
    const editable = (await call("/check-runs/1/escalated")).data;
    expect(editable).toHaveLength(4); expect(editable[0]).toMatchObject({ review_basis: AI_REVIEW_BASIS, review_grade: "Developing" });
    expect((await call("/check-runs/1")).data.stage_status.stages.semantic.status).toBe("done");
    expect((await call("/stats")).data).toMatchObject({ ready_to_decide_count: 1, escalations_awaiting_review: 0 });
    expect(network).not.toHaveBeenCalled();
  });
  it("migrates an untouched old case once and preserves notes, dismissals and the source", async () => {
    const state = fresh(); state.flags[0].annotation = "Private reading note"; state.flags[1].overridden = true; state.flags[1].override_reason = "Teacher's explanation"; store(state);
    const network = vi.fn(); window.fetch = network; installWorkspaceSample();
    expect((await call("/flags/1")).data.annotation).toBe("Private reading note");
    expect((await call("/flags/2")).data.overridden).toBe(true);
    expect(JSON.parse(sessionStorage.getItem(WORKSPACE_SAMPLE.storageKey)!).state.aiReviewVersion).toBe(AI_REVIEW_VERSION);
    window.fetch = network; installWorkspaceSample();
    expect((await call("/audit")).data.items.filter((event: { event_type: string }) => event.event_type === "ai_review_prepared")).toHaveLength(1);
  });
  it("does not overwrite a partially graded case, a decided case or a changed rubric", () => {
    const graded = fresh(); graded.report.results[0].outcome = "passed"; graded.report.results[0].score = 75;
    const decided = fresh(); decided.report.decision = "returned";
    const changed = fresh(); changed.rubric.criteria[0].weight = 7;
    const sourceMissing = fresh(); sourceMissing.fileAvailable = false;
    const anotherSource = fresh(); anotherSource.report.manuscript_id = 2;
    for (const state of [graded, decided, changed, sourceMissing, anotherSource]) {
      const before = structuredClone(state); expect(applyPreparedAiReview(state)).toBe(false); expect(state).toEqual(before);
    }
  });
  it("retains AI grades on cancel/reload, saves a true teacher confirmation and freezes a decided case", async () => {
    const network = vi.fn(); window.fetch = network; installWorkspaceSample();
    await call("/check-runs/1/escalated/101/resolve", { resolution: "mark_level", level: 2, reason: "" });
    let report = (await call("/check-runs/1/report")).data;
    expect(report.results[0]).toMatchObject({ basis: AI_REVIEW_CONFIRMED_BASIS, resolution: { type: "mark_level", reason: "" } });
    expect(report.pending_review_count).toBe(0); expect(report.composite_score).toBe(60);
    window.fetch = network; installWorkspaceSample();
    report = (await call("/check-runs/1/report")).data; expect(report.results[0].basis).toBe(AI_REVIEW_CONFIRMED_BASIS);
    expect((await call("/check-runs/1/escalated")).data).toHaveLength(3);
    await call("/check-runs/1/decision", { decision: "returned", note: "Revise the recorded issues." });
    expect((await call("/check-runs/1/escalated")).data).toEqual([]);
    expect((await call("/check-runs/1/escalated/102/resolve", { resolution: "mark_level", level: 4, reason: "" })).status).toBe(409);
    expect((await call("/check-runs/1/report")).data.decision).toBe("returned");
  });
  it("shares the completed AI provenance without private teacher text", async () => {
    window.fetch = vi.fn(); installWorkspaceSample();
    await call("/check-runs/1/escalated/101/resolve", { resolution: "mark_level", level: 3, reason: "PRIVATE-TEACHER-TEXT" });
    const share = (await call("/check-runs/1/share", { expires_at: null })).data;
    const shared = (await call(`/shared/${share.token}/report`)).data;
    expect(shared.report.results[1].basis).toBe(AI_REVIEW_BASIS);
    expect(JSON.stringify(shared)).not.toContain("PRIVATE-TEACHER-TEXT");
  });
  it.each(["run", "manuscript", "rubric", "family", "version", "evidence", "missing-run"])("refuses mismatched cached %s before grading or replacing saved evidence", async mismatch => {
    const state = fresh();
    if (mismatch === "run") state.run.id = 2;
    if (mismatch === "manuscript") state.manuscript.id = 2;
    if (mismatch === "rubric") state.rubric.id = 2;
    if (mismatch === "family") state.rubric.rubric_family_id = "another-format";
    if (mismatch === "version") state.rubric.version = 2;
    if (mismatch === "evidence") state.report.results[0].evidence[0].quote = "A different saved quotation";
    if (mismatch === "missing-run") delete (state as Partial<typeof state>).run;
    store(state); window.fetch = vi.fn(); installWorkspaceSample();
    const report = (await call("/check-runs/1/report")).data;
    expect(report.results).toEqual(state.report.results);
    expect(report.pending_review_count).toBe(4);
  });
  it("attributes retained reasoning to the earlier AI review after all four teacher changes", async () => {
    window.fetch = vi.fn(); installWorkspaceSample();
    for (let id = 1; id <= 4; id++) await call(`/check-runs/1/escalated/${100 + id}/resolve`, { resolution: "mark_level", level: 4, reason: "PRIVATE-CONFIRMATION" });
    const report = (await call("/check-runs/1/report")).data;
    expect(report.results.every((row: { basis: string; reasoning: string; level: { name: string } }) => row.basis === AI_REVIEW_CONFIRMED_BASIS && row.reasoning.startsWith("Earlier AI review (before the teacher's saved grade):") && row.level.name === "Above Average")).toBe(true);
    const share = (await call("/check-runs/1/share", { expires_at: null })).data;
    const shared = (await call(`/shared/${share.token}/report`)).data;
    expect(shared.report.results[0].reasoning).toMatch(/^Earlier AI review/);
    expect(shared.report.results[0].basis).toBe(AI_REVIEW_CONFIRMED_BASIS);
    expect(JSON.stringify(shared)).not.toContain("PRIVATE-CONFIRMATION");
  });
  it.each([null, {}])("safely initializes missing stage containers: %s", async stage => {
    const state = fresh(); state.run.stage_status = stage; store(state);
    window.fetch = vi.fn(); installWorkspaceSample();
    expect((await call("/check-runs/1/report")).data.pending_review_count).toBe(0);
    expect((await call("/check-runs/1")).data.stage_status.stages.semantic.status).toBe("done");
  });
});
