import type { AuditLogDetail, FlagOut, ReportOut, ShareLinkOut } from "../api/types";
import { companionSource, sampleCriteria, sampleEscalatedItems, sampleFlags, sampleInstructor, sampleLibrary, sampleManuscript, sampleReport, sampleRubric, sampleRun, WORKSPACE_SAMPLE } from "./workspaceFixture";
import { prepareWorkspaceSource } from "./workspacePreparation";
import { evidenceByImageId, evidenceFor, evidenceSourceHash, publicEvidenceImages, sourceEvidence } from "./sourceEvidence";
import { RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";

type SampleState = {
  run: typeof sampleRun;
  report: ReportOut;
  flags: FlagOut[];
  rubric: typeof sampleRubric;
  manuscript: typeof sampleManuscript;
  library: typeof sampleLibrary;
  share: ShareLinkOut | null;
  fileAvailable: boolean;
  events: AuditLogDetail[];
};
const fresh = (): SampleState => ({ run: structuredClone(sampleRun), report: structuredClone(sampleReport), flags: structuredClone(sampleFlags), rubric: structuredClone(sampleRubric), manuscript: structuredClone(sampleManuscript), library: structuredClone(sampleLibrary), share: null, fileAvailable: true, events: [] });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const failure = (message: string, status = 422) => json({ error: { code: status === 409 ? "conflict" : status === 410 ? "gone" : "sample_operation", message } }, status);
const textValue = (value: unknown) => typeof value === "string" ? value : "";
const sha256 = async (bytes: ArrayBuffer) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2, "0")).join("");

export function installWorkspaceSample() {
  const originalFetch = window.fetch.bind(window);
  let state = fresh();
  try {
    const stored = JSON.parse(sessionStorage.getItem(WORKSPACE_SAMPLE.storageKey) ?? "null");
    if (stored?.version === 1 && stored.state?.report?.manuscript_id === 1 && Array.isArray(stored.state?.flags) && Array.isArray(stored.state?.events) && Array.isArray(stored.state?.library) && Array.isArray(stored.state?.rubric?.criteria)) state = { ...fresh(), ...stored.state, run: stored.state.run ?? structuredClone(sampleRun) } as SampleState;
  } catch { /* An unavailable or invalid sample cache starts a clean sample. */ }
  // Add newly bound public evidence without losing existing practice judgments.
  state.report.results = state.report.results.map(row => ({ ...row, evidence: sampleReport.results.find(current => current.criterion_id === row.criterion_id)?.evidence ?? row.evidence }));
  state.flags = state.flags.map(flag => ({ ...flag, source_evidence: evidenceFor("flag", flag.id) }));
  function persist() { try { sessionStorage.setItem(WORKSPACE_SAMPLE.storageKey, JSON.stringify({ version: 1, state })); if (state.share) localStorage.setItem(`${WORKSPACE_SAMPLE.storageKey}:share:${state.share.token}`, JSON.stringify({ ...publicSnapshot(), expires_at: state.share.expires_at })); } catch { /* This visit still works when browser storage is disabled. */ } }
  function audit(event_type: string, payload: Record<string, unknown>) {
    state.events.unshift({ id: state.events.length + 1, event_type, check_run_id: state.report.check_run_id, manuscript_group_label: state.manuscript.group_label, manuscript_original_filename: WORKSPACE_SAMPLE.filename, prompt_type: null, prompt_version: null, agreement_score: null, llm_execution_mode: null, input_hash: null, created_at: new Date().toISOString(), payload: { sample: true, ...payload } });
  }
  if (!state.events.length) {
    audit("ingest_complete", { original_filename: WORKSPACE_SAMPLE.filename, page_count: WORKSPACE_SAMPLE.pageCount, source: WORKSPACE_SAMPLE.source, sha256: WORKSPACE_SAMPLE.sha256, note: "Unchanged publicly licensed source. Prepared review records; no live AI analysis." });
    audit("statistical_forensics", { outcome: "not_applicable", note: "This software implementation report does not supply a conventional inferential-statistics result for this prepared example. Numeric source code is not treated as a statistical test." });
    audit("document_review", { status: "needs_review", sample: true, llm_mode: "manual", note: "Full-document review: narrative, source code, figures, screenshots, references and component summary checked. Rubric levels remain for instructor judgment." });
  }

  let preparationController: AbortController | undefined;
  const isPreparing = () => !["done", "failed", "cancelled"].includes(state.run.status);
  function startSourcePreparation() {
    if (preparationController && isPreparing()) return;
    const controller = new AbortController();
    preparationController = controller;
    state.run = { ...structuredClone(sampleRun), status: "ingesting", started_at: new Date().toISOString(), finished_at: null, stage_status: { stages: { semantic: structuredClone(sampleRun.stage_status!.stages!.semantic) } } };
    state.manuscript.latest_check_run_status = "ingesting";
    audit("source_preparation_started", { note: "Loading the published original and checking page/excerpt bindings. Existing instructor judgments are retained. No model grading runs." });
    persist();
    void prepareWorkspaceSource(originalFetch, controller.signal, (key, note) => {
      if (controller !== preparationController || controller.signal.aborted) return;
      const stages = state.run.stage_status!.stages!;
      for (const entry of Object.values(stages)) if (entry.status === "running") entry.status = "done";
      stages[key] = { status: "running", note };
      state.run.status = key;
      state.manuscript.latest_check_run_status = key;
      persist();
    }).then(receipt => {
      if (controller !== preparationController || controller.signal.aborted) return;
      for (const entry of Object.values(state.run.stage_status!.stages!)) if (entry.status === "running") entry.status = "done";
      state.run.status = "done";
      state.run.finished_at = new Date().toISOString();
      state.manuscript.latest_check_run_status = "done";
      audit("source_preparation_complete", receipt);
      persist();
    }).catch((error: unknown) => {
      if (controller !== preparationController || controller.signal.aborted) return;
      state.run.status = "failed";
      state.run.finished_at = new Date().toISOString();
      state.run.stage_status!.failed = { code: "source_preparation_failed", message: error instanceof Error ? error.message : "The prepared source could not be verified." };
      state.manuscript.latest_check_run_status = "failed";
      audit("source_preparation_failed", { message: state.run.stage_status!.failed.message });
      persist();
    });
  }

  function recalculate() {
    const pending = state.report.results.filter(row => row.outcome === "escalated").length;
    state.report.pending_review_count = pending;
    const assessed = state.report.results.filter(row => row.outcome !== "not_applicable" && row.score !== null);
    const totalWeight = assessed.reduce((total, row) => total + row.weight, 0);
    const composite = !pending && totalWeight ? assessed.reduce((total, row) => total + (row.score ?? 0) * row.weight, 0) / totalWeight : null;
    state.report.composite_score = composite;
    state.report.status = pending || composite === null ? "needs_review" : composite >= state.report.thresholds.ready_min_score ? "ready" : composite < state.report.thresholds.not_ready_max_score ? "not_ready" : "conditionally_ready";
    state.report.reason = pending ? sampleReport.reason : "The readiness band follows the instructor's recorded rubric judgments. Read the source observations about implementation scope, component totals and supporting literature before recording a defense decision.";
    state.manuscript.escalations_awaiting_review = pending;
    state.manuscript.latest_readiness = state.report.status;
    state.manuscript.latest_decision = state.report.decision;
  }

  function projectPublicReport(report: ReportOut) {
    return {
      check_run_id: report.check_run_id, manuscript_group_label: report.manuscript_group_label,
      manuscript_original_filename: report.manuscript_original_filename, rubric_title: report.rubric_title,
      status: report.status, composite_score: report.composite_score,
      thresholds: { ready_min_score: report.thresholds.ready_min_score, not_ready_max_score: report.thresholds.not_ready_max_score },
      reason: report.reason, flag_deduction: report.flag_deduction, unresolved_high_flag_count: report.unresolved_high_flag_count,
      decision: report.decision, decided_at: report.decided_at, decision_note: report.decision_note,
      rubric_is_current: report.rubric_is_current, llm_mode: report.llm_mode,
      rubric_needs_review: report.rubric_needs_review, rubric_parse_issues: report.rubric_parse_issues,
      originality_scope: report.originality_scope,
      results: report.results.map(row => ({ criterion_id: row.criterion_id, text: row.text, type: row.type,
        weight: row.weight, weight_importance: row.weight_importance, kind: row.kind, outcome: row.outcome,
        score: row.score, basis: row.basis, anchor: row.anchor, reasoning: row.reasoning, reason: row.reason,
        evidence: row.evidence.map(item => ({ quote: item.quote, anchor: item.anchor })),
        level: row.level ? { name: row.level.name, ordinal: row.level.ordinal, points: row.level.points, max_points: row.level.max_points } : null,
        resolved: !!row.resolution || row.resolved === true }))
    };
  }
  function publicFlags(flags: (FlagOut & { problem_kind?: string | null })[]) {
    return flags.map(flag => ({ id: flag.id, check_kind: flag.check_kind, severity: flag.severity,
      criterion_text: flag.criterion_text, evidence_excerpt: flag.evidence_excerpt, page_anchor: flag.page_anchor,
      overridden: flag.overridden, is_passage_level: flag.is_passage_level, first_upload_context: flag.first_upload_context,
      confirmed_citation_source: flag.confirmed_citation_source, problem_kind: flag.ai_verdict_summary ?? flag.problem_kind ?? null, matched_ref: flag.matched_ref }));
  }
  function publicSnapshot() {
    return { report: projectPublicReport(state.report), flags: publicFlags(state.flags), evidence_images: state.fileAvailable ? publicEvidenceImages() : [] };
  }
  function readShared(token: string) {
    if (!/^sample-[0-9a-f-]{36}$/.test(token)) return null;
    const saved = JSON.parse(localStorage.getItem(`${WORKSPACE_SAMPLE.storageKey}:share:${token}`) ?? "null");
    if (!saved || saved.report?.check_run_id !== 1 || !Array.isArray(saved.report?.results) || !Array.isArray(saved.flags)) return null;
    if (saved.expires_at !== null && saved.expires_at !== undefined) {
      const expiry = typeof saved.expires_at === "string" ? Date.parse(saved.expires_at) : NaN;
      if (!Number.isFinite(expiry) || expiry <= Date.now()) return null;
    }
    const allowedIds = new Set(Array.isArray(saved.evidence_images) ? saved.evidence_images.map((item: { evidence_id?: unknown }) => item.evidence_id) : []);
    return { report: projectPublicReport(saved.report), flags: publicFlags(saved.flags), evidence_images: publicEvidenceImages().filter(item => allowedIds.has(item.evidence_id)) };
  }

  async function reportPdf() {
    const { jsPDF } = await import("jspdf");
    const pdf = new jsPDF({ unit: "mm", format: "a4", compress: true });
    const margin = 18, width = 174, bottom = 276, spacing = 5;
    let y = 20;
    const write = (value: string, heading = false) => {
      if (heading && y > bottom - 44) { pdf.addPage(); y = 20; }
      pdf.setFont("helvetica", heading ? "bold" : "normal"); pdf.setFontSize(heading ? 14 : 10);
      const lines: string[] = pdf.splitTextToSize(value.replace(/[^\x20-\x7E\n]/g, " "), width);
      for (const line of lines) { if (y > bottom) { pdf.addPage(); y = 20; } pdf.text(line, margin, y); y += heading ? 7 : spacing; }
      y += spacing;
    };
    write("VERIDICAL - Manuscript review", true);
    write("Review basis: full-document reading with page-bound observations. The instructor records the rubric levels and final decision.");
    write("Model grading skipped. Statistical forensics is not applicable to this implementation report. Originality comparison was not run; no similarity judgment is recorded.");
    write(WORKSPACE_SAMPLE.title, true); write(WORKSPACE_SAMPLE.attribution);
    write(`Source: ${WORKSPACE_SAMPLE.pageCount} pages. Unchanged original SHA-256: ${WORKSPACE_SAMPLE.sha256}`);
    write(`Readiness: ${state.report.status}. Instructor decision: ${state.report.decision ?? "Not recorded"}.`);
    if (state.report.decision_note) write(`Decision note: ${state.report.decision_note}`);
    write("Criterion record", true);
    for (const result of state.report.results) { write(`${result.text}: ${result.level?.name ?? result.outcome}`, true); write(result.resolution?.reason ?? result.reasoning ?? result.reason ?? "Needs instructor review."); for (const evidence of result.evidence) write(`${evidence.anchor}: ${evidence.quote}`); }
    write("Source observations", true);
    for (const flag of state.flags) { write(`${flag.check_kind} - ${flag.severity}${flag.overridden ? " - explained by instructor" : ""}`, true); write(`${flag.page_anchor}: ${flag.evidence_excerpt}`); write(flag.ai_reasoning ?? ""); for (const entry of evidenceFor("flag", flag.id)) write(`${entry.label}, ${entry.anchor}: ${entry.quote}`); if (flag.override_reason) write(`Instructor reason: ${flag.override_reason}`); if (flag.annotation) write(`Private instructor annotation: ${flag.annotation}`); }
    if (state.fileAvailable) {
      for (const entry of sourceEvidence) {
        if (entry.check_run_id !== state.report.check_run_id || entry.source_sha256 !== evidenceSourceHash || !entry.image_id) throw new Error("A source image is not bound to this report.");
        const source = await originalFetch(`/sample-assets/evidence/${entry.image_id}.png`, { credentials: "omit" });
        if (!source.ok) throw new Error("A manuscript evidence image could not be loaded.");
        pdf.addPage(); y = 17;
        pdf.setFont("helvetica", "bold"); pdf.setFontSize(10);
        const title: string[] = pdf.splitTextToSize(`${entry.subject.kind === "criterion" ? "Criterion" : "Finding"} ${entry.subject.id}: ${entry.label}`, width);
        for (const line of title) { pdf.text(line, margin, y); y += 5; }
        pdf.setFont("helvetica", "normal"); pdf.setFontSize(8);
        pdf.text(`${WORKSPACE_SAMPLE.filename} | PDF page ${entry.page} | ${entry.precision === "page_bbox" ? "Exact quotation marked" : "Page only; no exact mark"}`, margin, y); y += 5;
        const imageBuffer = await source.arrayBuffer();
        if (await sha256(imageBuffer) !== entry.image_sha256) throw new Error("The source image checksum does not match its evidence record.");
        const bytes = new Uint8Array(imageBuffer);
        const dimensions = pdf.getImageProperties(bytes);
        const imageHeight = Math.min(bottom - y, width * dimensions.height / dimensions.width);
        const imageWidth = imageHeight * dimensions.width / dimensions.height;
        pdf.addImage(bytes, "PNG", (210 - imageWidth) / 2, y, imageWidth, imageHeight, undefined, "FAST");
      }
    } else write("Manuscript page images are unavailable because the original file was removed from this browser record.");
    for (let index = 1; index <= pdf.getNumberOfPages(); index++) { pdf.setPage(index); pdf.setFontSize(8); pdf.text(`Document review - page ${index} of ${pdf.getNumberOfPages()}`, margin, 289); }
    return new Response(pdf.output("blob"), { headers: { "Content-Type": "application/pdf" } });
  }

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = input instanceof Request ? input.url : String(input);
    const url = new URL(raw, window.location.origin);
    // Static sample assets are the only allowed network requests from this adapter.
    if (url.origin === window.location.origin && (url.pathname.startsWith("/sample-assets/") || url.pathname.startsWith("/assets/"))) return originalFetch(input, { ...init, credentials: "omit" });
    const path = url.pathname.replace(/^\/api(?=\/|$)/, "");
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    let body: Record<string, unknown> = {};
    try { if (typeof init?.body === "string") body = JSON.parse(init.body) as Record<string, unknown>; } catch { return failure("The sample could not read this action."); }
    const paged = (items: unknown[]) => { const page = Number(url.searchParams.get("page") ?? 1); const size = Number(url.searchParams.get("page_size") ?? 20); return json({ items: items.slice((page - 1) * size, page * size), total: items.length, page, page_size: size }); };
    const write = (value: unknown) => { persist(); return json(value); };
    if (path === "/auth/session") return json({ instructor: sampleInstructor });
    if (path === "/auth/me" || path === "/auth/login" || path.startsWith("/auth/onboarding/")) return json(sampleInstructor);
    if (path === "/auth/logout") return json({ ok: true });
    if (path === "/auth/password" || path === "/settings/api-key") return failure("Account passwords and API keys are not changed in the sample workspace. Open the real workspace for account settings.");
    if (path === "/quota") return json({ mode: "fake", key_source: "shared", quota_day: "2026-10-26", calls_used: 0, daily_limit: 0, calls_remaining: 0, cache_hits_today: 0, cache_hit_rate: 0, reset_at: "2026-10-27T00:00:00Z", rpm_limit: 0, models: [] });
    if (path === "/programs") return json([{ id: 1, name: "Software Engineering" }, { id: 2, name: "Information Systems" }]);
    if (path === "/groups/collision-check") return json({ collision: false, existing_group_name: null });
    if (path === "/stats") return json({ manuscripts_checked: 1, ready_count: state.report.status === "ready" ? 1 : 0, conditionally_ready_count: state.report.status === "conditionally_ready" ? 1 : 0, not_ready_count: state.report.status === "not_ready" ? 1 : 0, needs_review_count: state.report.status === "needs_review" ? 1 : 0, escalations_awaiting_review: state.report.pending_review_count, escalation_rate: null, escalation_budget: 0, system_underperforming: false, decided_count: !isPreparing() && state.report.decision ? 1 : 0, ready_to_decide_count: !isPreparing() && !state.report.pending_review_count && !state.report.decision ? 1 : 0, needs_attention_count: !isPreparing() && state.report.pending_review_count && !state.report.decision ? 1 : 0, checking_count: isPreparing() ? 1 : 0 });
    if (path === "/manuscripts") {
      const queue = url.searchParams.get("queue"), query = (url.searchParams.get("q") ?? "").toLowerCase();
      let items = [state.manuscript];
      if (queue === "not_checked" || queue === "checking" && !isPreparing() || queue && queue !== "checking" && isPreparing() || queue === "complete" && !state.report.decision || queue === "needs_review" && (!state.report.pending_review_count || state.report.decision) || queue === "ready_to_decide" && (state.report.pending_review_count || state.report.decision)) items = [];
      return paged(items.filter(item => `${item.group_label} ${item.original_filename}`.toLowerCase().includes(query)));
    }
    const proposal = { title: { value: WORKSPACE_SAMPLE.title, anchor: "page 1" }, short_name: { value: "Stock & Inventory", anchor: "page 1" }, members: WORKSPACE_SAMPLE.authors.map(value => ({ value, anchor: "page 1" })), program: { value: "Software Engineering", anchor: "page 3" }, adviser: { value: "Md. Sakib Ali Mazumder", anchor: "page 1" }, extraction_failed: false };
    if (method === "POST" && ["/manuscripts/ingest", "/rubrics", "/library/entries"].includes(path)) {
      const file = init?.body instanceof FormData ? init.body.get("file") : null;
      if (!(file instanceof File)) return failure("Choose a manuscript file.");
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())), value => value.toString(16).padStart(2, "0")).join("");
      if (digest !== WORKSPACE_SAMPLE.sha256) return failure("This workspace uses the published 109-page manuscript. Download the original PDF from Source and review details to practice this upload. A different manuscript needs the account workspace for its own analysis.");
      if (path === "/rubrics") { state.rubric.title = url.searchParams.get("title") || sampleRubric.title; audit("rubric_uploaded", { filename: file.name, note: "Loaded prepared rubric from this original document's page 1; no AI parser ran." }); return write(state.rubric); }
      if (path === "/library/entries") return json({ outcome: "exact_duplicate", item: null, duplicate_of: { manuscript_id: 201, label: WORKSPACE_SAMPLE.title, original_filename: WORKSPACE_SAMPLE.filename, added_at: WORKSPACE_SAMPLE.preparedAt }, message: "This exact published manuscript is already in the sample Library. Open the existing record." });
      audit("ingest_complete", { filename: file.name, sha256: digest, duplicate: true, sample: true });
      return write({ manuscript_id: 1, group_label: state.manuscript.group_label, ingest_status: "done", page_count: WORKSPACE_SAMPLE.pageCount, anchor_kind: "page", image_only: false, text_chars: WORKSPACE_SAMPLE.inventory.textChars, images: WORKSPACE_SAMPLE.inventory.images, tables: WORKSPACE_SAMPLE.inventory.tables, equations: 0, citations: 0, vision_status: "none", notes: ["Original bytes match the bundled public source. Page, text, image-occurrence, and detected-table inventories were prepared using PyMuPDF. Equations, citations, and image meanings are not analyzed by this sample upload."], group_proposal: proposal, existing_upload: { manuscript_id: 1, created_at: WORKSPACE_SAMPLE.preparedAt, original_filename: WORKSPACE_SAMPLE.filename, purged_at: null } });
    }
    if (/^\/manuscripts\/\d+\/group-proposal$/.test(path)) return json(proposal);
    if (/^\/manuscripts\/\d+\/group$/.test(path) && method === "PATCH") { state.manuscript.group_label = textValue(body.group_name) || textValue(body.name) || textValue(body.group_label) || state.manuscript.group_label; state.report.manuscript_group_label = state.manuscript.group_label; audit("group_confirmed", { ...body, group_label: state.manuscript.group_label }); return write({ group_id: 1, group_label: state.manuscript.group_label, program: "Software Engineering", title: WORKSPACE_SAMPLE.title, matched: true }); }
    const rubricList = () => [{ ...state.rubric, created_at: WORKSPACE_SAMPLE.preparedAt, criteria_count: state.rubric.criteria.length, report_count: 1, program: "Software Engineering" }];
    if (path === "/rubric-families" || /^\/rubric-families\/[^/]+\/versions$/.test(path)) return json(rubricList());
    if (/^\/rubric-families\/[^/]+\/program$/.test(path)) return write(rubricList());
    if (/^\/rubrics\/\d+$/.test(path)) { if (method === "DELETE") return failure("The prepared format is retained so this sample can be reset."); return json(state.rubric); }
    if (/^\/rubrics\/\d+\/criteria$/.test(path) && method === "PUT") { if (!Array.isArray(body.criteria) || !body.criteria.length) return failure("Keep at least one criterion."); state.rubric.criteria = (body.criteria as typeof sampleCriteria).map((criterion, i) => ({ ...criterion, id: criterion.id ?? i + 1, position: i })); audit("rubric_confirmed", { criteria: state.rubric.criteria, note: "Sample format edits do not rerun the prepared report." }); return write(state.rubric); }
    if (/^\/rubrics\/\d+\/activate$/.test(path)) return write(state.rubric);
    if (path === "/settings") return json({ thresholds: { ...sampleReport.thresholds, escalation_agreement_threshold: .7, editable: false }, prompt_versions: [], api_key: { byok_available: false, has_own_api_key: false } });
    if (path === "/check-runs/rerun-estimate") return json({ items: [{ manuscript_id: 1, estimated_calls: 0 }], total_estimated_calls: 0, manuscripts_with_no_estimate: 0 });
    if (path === "/check-runs" && method === "POST") { startSourcePreparation(); return write(state.run); }
    if (/^\/check-runs\/\d+\/cancel$/.test(path) && method === "POST") { if (isPreparing()) { preparationController?.abort(); state.run.status = "cancelled"; state.run.cancel_requested_at = new Date().toISOString(); state.run.finished_at = state.run.cancel_requested_at; state.manuscript.latest_check_run_status = "cancelled"; audit("source_preparation_cancelled", { note: "Original manuscript and prior instructor judgments retained." }); } return write(state.run); }
    if (/^\/check-runs\/\d+$/.test(path)) return json({ ...state.run, manuscript_group_label: state.manuscript.group_label });
    if (/\/document\/file$/.test(path)) {
      if (!state.fileAvailable && path.startsWith("/check-runs/")) return failure("The original file was removed from this browser record.", 404);
      const source = path.startsWith(`/library/${companionSource.id}/`) ? companionSource : WORKSPACE_SAMPLE;
      const response = await originalFetch(source.pdfAsset, { credentials: "omit" });
      return new Response(response.body, { headers: { "Content-Type": "application/pdf", "Content-Length": response.headers.get("Content-Length") ?? "" } });
    }
    if (/\/document$/.test(path)) {
      const libraryId = path.startsWith("/library/") ? Number(path.split("/")[2]) : null;
      const source = libraryId === companionSource.id ? companionSource : WORKSPACE_SAMPLE;
      return json({ manuscript_id: libraryId ?? 1, original_filename: source === companionSource ? companionSource.filename : WORKSPACE_SAMPLE.filename, source_format: "pdf", available: state.fileAvailable || libraryId !== null, unavailable_reason: state.fileAvailable ? null : "purged", purged_at: state.fileAvailable ? null : new Date().toISOString(), source_sha256: source === WORKSPACE_SAMPLE ? evidenceSourceHash : companionSource.sha256, page_count: source.pageCount, regions: libraryId !== null ? [] : state.flags.map(flag => { const entry = evidenceFor("flag", flag.id)[0]; return { flag_id: flag.id, kind: entry?.precision ?? "page_only", page: entry?.page ?? Number(flag.page_anchor.match(/\d+/)?.[0] ?? 1), end_page: null, bbox: entry?.boxes[0] ?? null, all_bboxes: entry?.boxes ?? [], paragraph: null, index: null }; }) });
    }
    if (/\/document\/reuse-matches$/.test(path)) return json({ passage_archive_size_n: state.library.length, matches: [] });
    if (/\/report\/export\.pdf$/.test(path)) return reportPdf();
    if (/^\/check-runs\/\d+\/report$/.test(path)) return json(state.report);
    if (/^\/check-runs\/\d+\/flags$/.test(path)) return json(state.flags.map(flag => ({ ...flag, problem_kind: flag.ai_verdict_summary })));
    if (/^\/check-runs\/\d+\/escalated$/.test(path)) return json(sampleEscalatedItems.filter(item => state.report.results.some(row => row.criterion_id === item.criterion_id && row.outcome === "escalated")));
    if (/\/escalated\/\d+\/resolve$/.test(path) && method === "POST") {
      if (body.reason !== undefined && typeof body.reason !== "string") return failure("The instructor comment must be text.");
      const comment = textValue(body.reason).trim();
      if (!["mark_level", "mark_pass", "mark_fail", "needs_document"].includes(textValue(body.resolution))) return failure("Choose a supported instructor resolution.");
      if (body.resolution === "needs_document" && comment.length < RESOLUTION_REASON_MIN_LENGTH) return failure(`Enter a reason of at least ${RESOLUTION_REASON_MIN_LENGTH} characters.`);
      const resultId = Number(path.match(/escalated\/(\d+)/)?.[1]);
      const task = sampleEscalatedItems.find(item => item.check_result_id === resultId);
      if (!task) return failure("This criterion does not exist.", 404);
      const row = state.report.results.find(result => result.criterion_id === task.criterion_id)!;
      const criterion = sampleCriteria.find(item => item.id === task.criterion_id)!;
      const level = criterion.levels?.find(item => item.level === body.level);
      if (body.resolution === "mark_level" && !level) return failure("Choose one of this criterion's rubric levels.");
      row.outcome = body.resolution === "needs_document" ? "not_applicable" : body.resolution === "mark_fail" ? "failed" : "passed";
      row.score = row.outcome === "not_applicable" ? null : level ? level.points / criterion.weight * 100 : row.outcome === "passed" ? 100 : 0;
      row.level = level ? { name: level.name, ordinal: level.level, points: level.points, max_points: criterion.weight } : null;
      row.resolution = { type: body.resolution as NonNullable<typeof row.resolution>["type"], reason: comment, ai_majority_verdict: null };
      row.basis = "manual"; state.report.decision = null; recalculate(); audit("escalation_resolved", { criterion_id: row.criterion_id, resolution: body.resolution, reason: comment, level: body.level }); return write({ check_result_id: resultId, outcome: row.outcome, score: row.score, report: state.report });
    }
    if (/^\/check-runs\/\d+\/decision$/.test(path) && method === "POST") { if (state.report.pending_review_count) return failure("Resolve the outstanding criteria first.", 409); if (!["approved", "returned", "rejected"].includes(textValue(body.decision))) return failure("Choose an instructor decision."); state.report.decision = body.decision as ReportOut["decision"]; state.report.decision_note = textValue(body.note) || null; state.report.decided_at = new Date().toISOString(); recalculate(); audit("decision_recorded", { decision: body.decision, note: body.note }); return write(state.report); }
    if (/^\/check-runs\/\d+\/reopen$/.test(path) && method === "POST") { if (!textValue(body.reason).trim()) return failure("A reason is required."); audit("decision_reopened", { previous_decision: state.report.decision, reason: body.reason }); state.report.decision = null; state.report.decision_note = null; state.report.decided_at = null; recalculate(); return write(state.report); }
    const flagMatch = path.match(/^\/flags\/(\d+)(?:\/(annotate|override|confirm-source))?$/);
    if (flagMatch) { const flag = state.flags.find(item => item.id === Number(flagMatch[1])); if (!flag) return failure("This sample finding does not exist.", 404); if (method !== "GET") { if (flagMatch[2] === "annotate") flag.annotation = textValue(body.annotation); else { if (!textValue(body.reason).trim()) return failure("Enter your reason."); flag.overridden = true; flag.override_reason = textValue(body.reason); } audit(flagMatch[2] === "annotate" ? "flag_annotated" : "flag_overridden", { flag_id: flag.id, ...body }); persist(); } return json(flagMatch[2] === "override" || flagMatch[2] === "confirm-source" ? { ...flag, report: state.report } : flag); }
    if (/^\/check-runs\/\d+\/share$/.test(path)) {
      try {
        if (method === "DELETE") { if (state.share) localStorage.removeItem(`${WORKSPACE_SAMPLE.storageKey}:share:${state.share.token}`); state.share = null; return write({ ok: true }); }
        if (method === "POST") {
          if (body.expires_at !== undefined && body.expires_at !== null && (typeof body.expires_at !== "string" || !Number.isFinite(Date.parse(body.expires_at)) || Date.parse(body.expires_at) <= Date.now())) return failure("Choose a valid future expiry or no expiry.");
          if (state.share) localStorage.removeItem(`${WORKSPACE_SAMPLE.storageKey}:share:${state.share.token}`);
          state.share = { token: `sample-${crypto.randomUUID()}`, check_run_id: 1, created_at: new Date().toISOString(), expires_at: textValue(body.expires_at) || null };
          localStorage.setItem(`${WORKSPACE_SAMPLE.storageKey}:share:${state.share.token}`, JSON.stringify({ ...publicSnapshot(), expires_at: state.share.expires_at }));
          audit("share_created", { note: "Read-only snapshot in this browser's local storage, not a public server capability. Private criterion reasons and annotations are omitted." }); persist();
        }
        return json(state.share);
      } catch { return failure("This browser cannot store a local sample share. No public report was published."); }
    }
    const sharedImage = path.match(/^\/shared\/([^/]+)\/evidence-images\/(e-[a-f0-9]{24})$/);
    if (sharedImage) {
      try {
        const shared = readShared(sharedImage[1]);
        if (!shared) return failure("This local sample share has expired or was revoked.", 410);
        if (url.searchParams.size || !shared.evidence_images.some(item => item.image_id === sharedImage[2])) return failure("This image is not part of the shared report.", 404);
        const entry = evidenceByImageId(sharedImage[2]);
        if (!entry || entry.check_run_id !== shared.report.check_run_id || entry.source_sha256 !== evidenceSourceHash) return failure("This source image is unavailable.", 404);
        const response = await originalFetch(`/sample-assets/evidence/${entry.image_id}.png`, { credentials: "omit", signal: init?.signal });
        if (!response.ok) return failure("This source image could not be loaded.", 404);
        const bytes = await response.arrayBuffer();
        if (await sha256(bytes) !== entry.image_sha256) return failure("The source image checksum does not match.", 422);
        return new Response(bytes, { headers: { "Content-Type": "image/png", "Cache-Control": "no-store" } });
      } catch { return failure("This shared image is unavailable.", 410); }
    }
    if (/^\/shared\/[^/]+\/report$/.test(path)) {
      try { const shared = readShared(path.split("/")[2]); return shared ? json(shared) : failure("This local sample share has expired or was revoked. It opens only in the browser where it was created.", 410); } catch { return failure("The local sample report could not be opened.", 410); }
    }

    if (path === "/library") { const query = (url.searchParams.get("search") ?? "").toLowerCase(); return paged(url.searchParams.get("scope") === "pending" ? [] : state.library.filter(item => `${item.title} ${item.authors.join(" ")}`.toLowerCase().includes(query) && (!url.searchParams.get("program") || item.program === url.searchParams.get("program")))); }
    const libraryMatch = path.match(/^\/library\/(\d+)(?:\/(remove|decision|excerpt))?$/);
    if (libraryMatch) { const item = state.library.find(entry => entry.manuscript_id === Number(libraryMatch[1])); if (!item) return failure("The sample Library record is unavailable.", 404); if (method === "PATCH") { item.title = textValue(body.title) || item.title; item.team_name = textValue(body.team_name) || item.team_name; if (Array.isArray(body.authors)) item.authors = body.authors.filter((value): value is string => typeof value === "string"); audit("library_updated", { manuscript_id: item.manuscript_id }); return write(item); } if (libraryMatch[2] === "remove") { state.library = state.library.filter(entry => entry.manuscript_id !== item.manuscript_id); audit("library_removed", { manuscript_id: item.manuscript_id }); return write({ manuscript_id: item.manuscript_id, status: "removed", added_at: item.added_at, removed_at: new Date().toISOString(), file_deleted: true }); } if (libraryMatch[2] === "excerpt") return json({ manuscript_id: item.manuscript_id, chapters: [{ chapter_index: 0, title: item.manuscript_id === companionSource.id ? "Abstract - page 2" : "Abstract - page 5", excerpt: item.manuscript_id === companionSource.id ? "This capstone is centered around the development of an efficient electronic property inventory system tailored for school assets" : "This project develops a robust, console-based application using", context_before: null, context_after: null }], total_chapters: 1, limitations: "Original published work. Read the complete document for context; no similarity finding is inferred from this excerpt.", purged_at: null }); return json(item); }
    if (path === "/audit") { const type = url.searchParams.get("event_type"); return paged(type ? state.events.filter(event => event.event_type === type) : state.events); }
    if (/^\/audit\/\d+$/.test(path)) return json(state.events.find(event => event.id === Number(path.split("/")[2])) ?? state.events[0]);
    // Never fall through to a live account or analysis service from sample mode.
    return failure("This operation is not connected in the sample workspace. Your real account and files have not changed.");
  };
  if (isPreparing()) startSourcePreparation();
  persist();
}
