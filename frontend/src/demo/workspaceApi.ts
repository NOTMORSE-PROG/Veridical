import type { AuditLogDetail, CriterionResultOut, ReportOut } from '../api/types';
import type { JudgmentContext, JudgmentHistoryEntry, JudgmentSnapshot } from '../api/judgments';
import type { EvidenceReference } from '../report/evidenceTypes';
import { librarySources, sampleFlags, sampleInstructor, sampleLibrary, sampleManuscript, sampleReport, unassessedReport, sampleRubric, sampleRun, WORKSPACE_SAMPLE } from './workspaceFixture';
import official from './officialRubric.json';
import review from './reviewData.json';
import assetManifest from './assets.json';
import assessed from './assessedReview.json';
import { RESOLUTION_REASON_MIN_LENGTH } from '../config/ui';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const failure = (message: string, status = 422) => json({ error: { code: status === 409 ? 'conflict' : 'demo_operation', message } }, status);
const hash = async (value: string | ArrayBuffer) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', typeof value === 'string' ? new TextEncoder().encode(value) : value)), b => b.toString(16).padStart(2, '0')).join('');
const fresh = (preassessed = true) => {
  const report = structuredClone(preassessed ? sampleReport : unassessedReport);
  const judgments: Record<number, JudgmentSnapshot> = {};
  const histories: Record<number, JudgmentHistoryEntry[]> = {};
  const events: AuditLogDetail[] = [];
  for (const [index, row] of report.results.entries()) {
    if (!row.resolution) continue;
    const after: JudgmentSnapshot = { judgment_revision: 1, resolution_type: row.resolution.type, outcome: 'passed', score: row.score,
      level: row.level ?? null, reason: row.resolution.reason, actor_id: sampleInstructor.id, recorded_at: assessed.reviewed_at, provenance: 'recorded' };
    judgments[row.criterion_id] = after;
    histories[row.criterion_id] = [{ schema_version: 1, action: 'initial_resolution', entry_id: index + 1, check_run_id: 1,
      criterion_id: row.criterion_id, check_result_id: row.check_result_id!, assessment_revision_before: index + 1,
      assessment_revision_after: index + 2, before: null, after, performed_by: sampleInstructor.id, performed_at: assessed.reviewed_at }];
    events.push({ id: index + 1, event_type: 'criterion_resolved', check_run_id: 1,
      manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename,
      prompt_type: null, prompt_version: null, agreement_score: null, llm_execution_mode: null, input_hash: null,
      created_at: assessed.reviewed_at, payload: { criterion_id: row.criterion_id, level: row.level?.name, reason: row.resolution.reason, method: assessed.method } });
  }
  return { report, flags: structuredClone(sampleFlags), rubric: structuredClone(sampleRubric),
    manuscript: { ...structuredClone(sampleManuscript), latest_readiness: report.status, escalations_awaiting_review: report.pending_review_count },
    library: structuredClone(sampleLibrary), events: events.reverse(), judgments, histories };
};

/** Local practice transport. Only reviewed, immutable public assets leave this adapter. */
export function installWorkspaceSample({ preassessed = true }: { preassessed?: boolean } = {}) {
  const network = window.fetch.bind(window);
  const xhrOpen = XMLHttpRequest.prototype.open;
  let state = fresh(preassessed);
  try {
    const saved = JSON.parse(sessionStorage.getItem(WORKSPACE_SAMPLE.storageKey) ?? 'null');
    if (saved?.schema === 1 && saved.state?.report?.results?.length === sampleRubric.criteria.length
      && saved.state.report.results.every((r: CriterionResultOut, i: number) => r.criterion_id === sampleRubric.criteria[i].id)
      && Number.isInteger(saved.state.report.assessment_revision)) state = { ...fresh(preassessed), ...saved.state };
  } catch { /* A blocked storage area leaves this visit usable in memory. */ }
  const persist = () => { try { sessionStorage.setItem(WORKSPACE_SAMPLE.storageKey, JSON.stringify({ schema: 1, state })); } catch { /* Memory-only practice is still available. */ } };
  const revision = () => state.report.assessment_revision!;
  const audit = (event: string, payload: Record<string, unknown>) => {
    state.events.unshift({ id: state.events.length + 1, event_type: event, check_run_id: 1,
      manuscript_group_label: WORKSPACE_SAMPLE.title, manuscript_original_filename: WORKSPACE_SAMPLE.filename,
      prompt_type: null, prompt_version: null, agreement_score: null, llm_execution_mode: null,
      input_hash: null, created_at: new Date().toISOString(), payload: { storage: 'browser_session', ...payload } });
    persist();
  };
  if (!state.events.length) audit('review_opened', { source_sha256: WORKSPACE_SAMPLE.sha256, rubric: official.title,
    criteria: sampleRubric.criteria.length, note: 'Source observations and criteria await instructor judgment. Automated assessment unavailable.' });
  const paginate = (items: unknown[], url: URL) => { const page = Math.max(1, Number(url.searchParams.get('page')) || 1); const size = Math.max(1, Number(url.searchParams.get('page_size')) || 20); return json({ items: items.slice((page - 1) * size, page * size), total: items.length, page, page_size: size }); };
  const checkedAssets = new Map<string, ArrayBuffer>();
  async function pdf(path: string, identity?: string) {
    const expected = assetManifest.find(asset => asset.path === path);
    if (!expected) return failure('This original document is unavailable.', 404);
    let bytes = checkedAssets.get(path);
    if (!bytes) {
      const response = await network(path, { credentials: 'omit' });
      if (!response.ok || response.headers.get('Content-Type')?.split(';')[0] !== 'application/pdf') return failure('The original PDF could not be loaded. Try again.', 503);
      bytes = await response.arrayBuffer();
      if (new TextDecoder().decode(bytes.slice(0, 5)) !== '%PDF-' || await hash(bytes) !== expected.sha256) return failure('The original PDF did not match its published source.', 409);
      checkedAssets.set(path, bytes);
    }
    return new Response(bytes.slice(0), { headers: { 'Content-Type': 'application/pdf', 'Content-Length': String(bytes.byteLength), ...(identity ? { 'X-Evidence-Source-Identity': identity } : {}) } });
  }
  function recalculate() {
    const rows = state.report.results;
    state.report.pending_review_count = rows.filter(r => r.outcome === 'escalated').length;
    const graded = rows.filter(r => r.score !== null && r.outcome !== 'not_applicable');
    const weight = graded.reduce((n, r) => n + r.weight, 0);
    const score = !state.report.pending_review_count && weight ? graded.reduce((n, r) => n + r.score! * r.weight, 0) / weight : null;
    state.report.composite_score = score;
    state.report.status = score === null ? 'needs_review' : score >= state.report.thresholds.ready_min_score ? 'ready' : score < state.report.thresholds.not_ready_max_score ? 'not_ready' : 'conditionally_ready';
    state.manuscript.latest_readiness = state.report.status;
    state.manuscript.escalations_awaiting_review = state.report.pending_review_count;
    state.manuscript.latest_decision = state.report.decision;
  }
  function context(row: CriterionResultOut): JudgmentContext {
    const original = unassessedReport.results.find(r => r.criterion_id === row.criterion_id)!;
    return { check_run_id: 1, criterion_id: row.criterion_id, check_result_id: row.check_result_id!, criterion_text: row.text,
      assessment_revision: revision(), decision: state.report.decision, can_revise: !state.report.decision && !!state.judgments[row.criterion_id],
      can_record: false, unavailable_reason: state.report.decision ? 'Reopen this decision first.' : null,
      current: state.judgments[row.criterion_id] ?? null,
      choices: { kind: 'levelled', levels: sampleRubric.criteria.find(c => c.id === row.criterion_id)!.levels!, exclusion_allowed: true, unavailable_reason: null },
      original_assessment: { kind: original.kind, basis: original.basis, reason: original.reason, reasoning: original.reasoning,
        ai_majority_verdict: null, evidence: original.evidence, unverified_evidence: [], support_withheld: false,
        support_status: null, evidence_unavailable: false, original_outcome: original.outcome, original_score: null, original_level: null } };
  }
  function record(row: CriterionResultOut, body: Record<string, unknown>) {
    if (state.report.decision || body.expected_revision !== revision()) return failure('This report changed. Reload it before saving.', 409);
    const current = state.judgments[row.criterion_id];
    if (current && body.expected_judgment_revision !== current.judgment_revision) return failure('This grade changed. Reload it before saving.', 409);
    const reason = typeof body.reason === 'string' ? body.reason.trim() : '';
    if ((current || body.resolution === 'needs_document') && reason.length < RESOLUTION_REASON_MIN_LENGTH) return failure('Explain your judgment before saving.');
    const criterion = sampleRubric.criteria.find(c => c.id === row.criterion_id)!;
    const level = criterion.levels!.find(l => l.level === body.level);
    if (body.resolution !== 'needs_document' && (body.resolution !== 'mark_level' || !level)) return failure('Choose one of this criterion’s named levels.');
    const before = revision();
    row.level = level && body.resolution === 'mark_level' ? { name: level.name, ordinal: level.level, points: level.points, max_points: Math.max(...criterion.levels!.map(l => l.points)) } : null;
    row.score = row.level ? row.level.points / row.level.max_points * 100 : null;
    row.outcome = row.level ? 'passed' : 'not_applicable';
    row.resolution = { type: body.resolution as 'mark_level' | 'needs_document', reason, ai_majority_verdict: null };
    row.judgment_revision = (current?.judgment_revision ?? 0) + 1;
    row.basis = 'instructor';
    state.report.assessment_revision = before + 1;
    const after: JudgmentSnapshot = { judgment_revision: row.judgment_revision, resolution_type: row.resolution.type,
      outcome: row.outcome, score: row.score, level: row.level, reason, actor_id: sampleInstructor.id,
      recorded_at: new Date().toISOString(), provenance: 'recorded' };
    const entry: JudgmentHistoryEntry = { schema_version: 1, action: current ? 'judgment_revised' : 'initial_resolution', entry_id: state.events.length + 1,
      check_run_id: 1, criterion_id: row.criterion_id, check_result_id: row.check_result_id!, assessment_revision_before: before,
      assessment_revision_after: revision(), before: current ?? null, after, performed_by: sampleInstructor.id,
      performed_at: after.recorded_at!, original_assessment: null };
    state.judgments[row.criterion_id] = after;
    state.histories[row.criterion_id] = [entry, ...(state.histories[row.criterion_id] ?? [])];
    recalculate(); audit(entry.action, { criterion_id: row.criterion_id, reason, level: row.level?.name });
    return { context: context(row), entry, report: state.report };
  }
  const metadata = (asset: typeof assetManifest[number], id: number) => ({ manuscript_id: id,
    original_filename: asset.path.split('/').at(-1), source_format: 'pdf', available: true, unavailable_reason: null,
    purged_at: null, source_sha256: asset.sha256, page_count: asset.pages, regions: [] });
  // Progress uploads use XHR rather than fetch. Reject them before any bytes leave.
  XMLHttpRequest.prototype.open = function () { throw new DOMException('New file uploads require a signed-in instructor account.', 'NotSupportedError'); };
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const path = url.pathname.replace(/^\/api(?=\/|$)/, '');
    if (url.origin !== window.location.origin) return failure('This workspace cannot connect to another service.', 403);
    if (method === 'GET' && assetManifest.some(a => a.path === url.pathname)) return pdf(url.pathname);
    if (method === 'GET' && /^\/(sample-assets|assets)\//.test(url.pathname) && !url.pathname.endsWith('.pdf')) return network(url.pathname, { credentials: 'omit', signal: init?.signal });
    let body: Record<string, unknown> = {};
    if (typeof init?.body === 'string') { try { body = JSON.parse(init.body); } catch { return failure('This action could not be read.'); } }
    if (path === '/auth/session') return json({ instructor: sampleInstructor });
    if (path === '/auth/me' || path.startsWith('/auth/onboarding/')) return json(sampleInstructor);
    if (path === '/auth/logout') return json({ ok: true });
    if (path === '/quota') return json({ mode: 'unknown', key_state: 'unavailable', key_source: 'shared', quota_day: WORKSPACE_SAMPLE.preparedAt.slice(0, 10), calls_used: 0, calls_remaining: 0, daily_limit: 0, rpm_limit: 0, cache_hits_today: 0, cache_hit_rate: null, reset_at: WORKSPACE_SAMPLE.preparedAt, models: [] });
    if (path === '/programs') return json([{ id: 1, name: 'Software Engineering' }]);
    if (path === '/stats') return json({ manuscripts_checked: 1, ready_count: Number(state.report.status === 'ready'), conditionally_ready_count: Number(state.report.status === 'conditionally_ready'), not_ready_count: Number(state.report.status === 'not_ready'), needs_review_count: Number(state.report.status === 'needs_review'), escalations_awaiting_review: state.report.pending_review_count, escalation_rate: null, escalation_budget: 0, system_underperforming: false, decided_count: Number(!!state.report.decision), ready_to_decide_count: Number(!state.report.pending_review_count && !state.report.decision), needs_attention_count: Number(!!state.report.pending_review_count && !state.report.decision), checking_count: 0, complete_queue_count: Number(!!state.report.decision) });
    if (path === '/manuscripts' && method === 'GET') {
      const queue = url.searchParams.get('queue'); const query = (url.searchParams.get('q') ?? '').toLowerCase();
      const visible = !['not_checked', 'checking'].includes(queue ?? '') && (queue !== 'complete' || !!state.report.decision)
        && (queue !== 'needs_review' || !!state.report.pending_review_count && !state.report.decision)
        && (queue !== 'ready_to_decide' || !state.report.pending_review_count && !state.report.decision);
      return paginate(visible && WORKSPACE_SAMPLE.title.toLowerCase().includes(query) ? [state.manuscript] : [], url);
    }
    if (path === '/check-runs/1') return json(sampleRun);
    if (path === '/check-runs/1/report' && method === 'GET') return json(state.report);
    if (path === '/check-runs/1/flags' && method === 'GET') return json(state.flags.map(f => ({ ...f, assessment_revision: revision(), problem_kind: f.ai_verdict_summary })));
    if (path === '/check-runs/1/escalated') return json(state.report.results.filter(r => r.outcome === 'escalated').map(r => ({ assessment_revision: revision(), check_result_id: r.check_result_id, criterion_id: r.criterion_id, criterion_text: r.text, weight: r.weight, agreement: null, votes: [], ai_majority_verdict: null, reason: r.reason, reasoning: r.reasoning, evidence: r.evidence, review_reason: 'not_graded', unverified_evidence: null, levels: sampleRubric.criteria.find(c => c.id === r.criterion_id)!.levels })));
    if (path === '/check-runs/1/document') return json({ ...metadata(assetManifest[0], 1), regions: state.flags.map(flag => ({ flag_id: flag.id,
      kind: 'page_only', page: Number(flag.page_anchor?.match(/\d+/)?.[0]), end_page: null, bbox: null, all_bboxes: [], paragraph: null, index: null })) });
    if (path === '/check-runs/1/document/file') return pdf(WORKSPACE_SAMPLE.pdfAsset);
    if (path === '/check-runs/1/document/reuse-matches') return json({ passage_archive_size_n: state.library.length, matches: [] });
    if (path.startsWith('/check-runs/1/evidence')) {
      const snapshot = await hash(JSON.stringify({ dataset: WORKSPACE_SAMPLE.storageKey, revision: revision(), references: review.references }));
      if (path === '/check-runs/1/evidence') return json({ snapshot_id: snapshot, assessment_revision: revision(), llm_mode: 'unknown', references: review.references });
      if (path === '/check-runs/1/evidence/source-file') return url.searchParams.get('source_identity') === WORKSPACE_SAMPLE.sha256 ? pdf(WORKSPACE_SAMPLE.pdfAsset, WORKSPACE_SAMPLE.sha256) : failure('The source identity changed.', 409);
      const match = path.match(/^\/check-runs\/1\/evidence\/([a-f0-9]{64})\/(location|page)$/);
      const reference = match && (review.references as EvidenceReference[]).find(r => r.reference_id === match[1]);
      if (!match || !reference) return failure('This evidence is not in this report.', 404);
      if (url.searchParams.get('snapshot_id') !== snapshot) return failure('This report changed. Reload its evidence.', 409);
      const placement = review.placements[match[1] as keyof typeof review.placements];
      const caption = `Original manuscript page ${placement.page}. Source excerpt for instructor review.`;
      if (match[2] === 'location') return json({ snapshot_id: snapshot, source_identity: WORKSPACE_SAMPLE.sha256, source_format: 'pdf', placement, caption });
      const image = review.page_images[String(placement.page) as keyof typeof review.page_images];
      const response = await network(image.path, { credentials: 'omit' });
      if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/png')) return failure('The original page image could not be loaded.', 503);
      const bytes = await response.arrayBuffer();
      if (await hash(bytes) !== image.sha256) return failure('The original page image changed.', 409);
      let binary = ''; for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
      return json({ snapshot_id: snapshot, placement, caption, image_data_url: `data:image/png;base64,${btoa(binary)}`, image_sha256: image.sha256 });
    }
    const judgment = path.match(/^\/check-runs\/1\/criteria\/(\d+)\/judgment(?:\/(records|revisions|history))?$/);
    if (judgment) {
      const row = state.report.results.find(r => r.criterion_id === Number(judgment[1]));
      if (!row) return failure('This criterion is not in the active rubric.', 404);
      if (method === 'GET' && judgment[2] === 'history') return json({ check_run_id: 1, criterion_id: row.criterion_id, check_result_id: row.check_result_id, assessment_revision: revision(), judgment_revision: row.judgment_revision, current: state.judgments[row.criterion_id], entries: state.histories[row.criterion_id] ?? [], history_incomplete: false, next_cursor: null });
      if (method === 'GET') return json(context(row));
      if (method === 'POST' && judgment[2] === 'revisions' && state.judgments[row.criterion_id]) { const result = record(row, body); return result instanceof Response ? result : json(result); }
      if (method !== 'GET') return failure('Use the pending criterion review to record its first judgment.', 409);
    }
    const resolve = path.match(/^\/check-runs\/1\/escalated\/(\d+)\/resolve$/);
    if (resolve && method === 'POST') { const row = state.report.results.find(r => r.check_result_id === Number(resolve[1])); if (!row) return failure('This result is unavailable.', 404); const result = record(row, body); return result instanceof Response ? result : json({ check_result_id: row.check_result_id, outcome: row.outcome, score: row.score, report: state.report }); }
    if (['/check-runs/1/decision', '/check-runs/1/reopen'].includes(path) && method === 'POST') {
      if (body.expected_revision !== revision()) return failure('Reload the report before recording a decision.', 409);
      const previousDecision = state.report.decision;
      const previousNote = state.report.decision_note;
      if (path.endsWith('/reopen')) {
        if (!state.report.decision || typeof body.reason !== 'string' || body.reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) return failure('Explain why this decision needs to be reopened.');
        state.report.decision = null; state.report.decided_at = null; state.report.decision_note = null;
      } else {
        if (state.report.decision) return failure('Reopen the existing decision before changing it.', 409);
        if (!['approved', 'returned', 'rejected'].includes(String(body.decision))) return failure('Choose a decision.');
        if (body.decision !== 'returned' && state.report.pending_review_count) return failure('Review all criteria before approving or rejecting this record.');
        const note = typeof body.note === 'string' ? body.note.trim() : '';
        const needsReason = (body.decision === 'returned' && state.report.pending_review_count > 0)
          || (body.decision === 'approved' && state.report.status !== 'ready')
          || (body.decision === 'rejected' && ['ready', 'needs_review'].includes(state.report.status));
        if (needsReason && note.length < RESOLUTION_REASON_MIN_LENGTH) return failure('Explain your decision before saving.');
        state.report.decision = body.decision as ReportOut['decision']; state.report.decided_at = new Date().toISOString(); state.report.decision_note = note || null;
      }
      state.report.assessment_revision = revision() + 1; recalculate();
      audit(path.endsWith('/reopen') ? 'report_reopened' : 'decision_recorded', { decision: state.report.decision, note: state.report.decision_note,
        previous_decision: previousDecision, previous_note: previousNote, reason: body.reason ?? null }); return json(state.report);
    }
    if (path === '/rubric-families' || path === `/rubric-families/${official.rubric_family_id}/versions`) return json([{ id: 1, rubric_family_id: official.rubric_family_id, version: 1, title: official.title, is_active: true, created_at: WORKSPACE_SAMPLE.preparedAt, criteria_count: official.criteria.length, report_count: 1, program: null }]);
    if (path === '/rubrics/1' || path === '/rubrics/1/activate') return json(state.rubric);
    if (path === '/rubrics/1/document') return json({ rubric_id: 1, rubric_family_id: official.rubric_family_id, version: 1, available: true, source_format: 'pdf', source_sha256: official.source_sha256, unavailable_reason: null, reconstruction: 'original_pdf' });
    if (['/rubrics/1/document/file', '/rubrics/1/document/original'].includes(path)) return url.searchParams.get('source_sha256') === official.source_sha256 ? pdf(official.source_asset) : failure('The requested rubric source does not match.', 409);
    if (path === '/library' && method === 'GET') return paginate(state.library.filter(item =>
      (!url.searchParams.get('scope') || url.searchParams.get('scope') === 'all' || item.status === url.searchParams.get('scope'))
      && (!url.searchParams.get('program') || item.program === url.searchParams.get('program'))
      && `${item.title} ${item.authors.join(' ')}`.toLowerCase().includes((url.searchParams.get('search') ?? '').toLowerCase())), url);
    const library = path.match(/^\/library\/(\d+)(?:\/(.*))?$/);
    if (library) {
      const item = state.library.find(i => i.manuscript_id === Number(library[1])); const source = librarySources.find(i => i.id === Number(library[1]));
      if (!item || !source) return failure('This source is not in the Library.', 404);
      if (!library[2] && method === 'GET') return json(item);
      if (library[2] === 'document') return json(metadata(source.asset, source.id));
      if (['document/file', 'document/original'].includes(library[2])) return pdf(source.asset.path);
      if (library[2] === 'excerpt') return json({ manuscript_id: source.id, label: source.title, excerpt: 'Open the original published document to read this Library source.', max_chars: null });
    }
    if (path === '/audit') return paginate(state.events, url);
    const event = path.match(/^\/audit\/(\d+)$/); if (event) return json(state.events.find(e => e.id === Number(event[1])) ?? null);
    const flag = path.match(/^\/flags\/(\d+)(?:\/(.*))?$/);
    if (flag) {
      const found = state.flags.find(f => f.id === Number(flag[1]));
      if (!found) return failure('This finding is not in this report.', 404);
      if (method === 'GET' && !flag[2]) return json({ ...found, assessment_revision: revision() });
      if (method === 'POST' && flag[2] === 'annotate') {
        if (state.report.decision) return failure('Reopen this decision before editing its findings.', 409);
        found.annotation = typeof body.annotation === 'string' ? body.annotation : null;
        audit('flag_annotated', { flag_id: found.id }); return json({ ...found, assessment_revision: revision() });
      }
      if (method === 'POST' && ['override', 'confirm-finding', 'confirm-source'].includes(flag[2])) {
        if (state.report.decision || body.expected_revision !== revision()) return failure('Review the current assessment before changing this finding.', 409);
        if (flag[2] !== 'confirm-finding' && (typeof body.reason !== 'string' || body.reason.trim().length < RESOLUTION_REASON_MIN_LENGTH)) return failure('Explain your judgment before saving.');
        if (flag[2] === 'confirm-source' && found.check_kind !== 'citation_integrity') return failure('This finding is not a citation.');
        found.overridden = flag[2] !== 'confirm-finding';
        found.override_reason = found.overridden ? String(body.reason) : null;
        found.finding_confirmed = !found.overridden;
        found.confirmed_citation_source = flag[2] === 'confirm-source';
        state.report.assessment_revision = revision() + 1;
        audit(`finding_${flag[2]}`, { flag_id: found.id, reason: body.reason ?? body.note });
        return json({ ...found, assessment_revision: revision(), report: state.report });
      }
    }
    if (path === '/check-runs/1/share' && method === 'GET') return json(null);
    if (path === '/archive' && method === 'GET') return paginate([{ ...state.manuscript, manuscript_id: 1, purged_at: null, removed_at: null, source_format: 'pdf', stored_file_bytes: assetManifest[0].bytes }], url);
    if (path === '/groups' && method === 'GET') return paginate([], url);
    return failure('This action requires a signed-in instructor account.');
  };
  persist();
  return () => { window.fetch = network; XMLHttpRequest.prototype.open = xhrOpen; };
}
