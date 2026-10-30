/// <reference types="node" />
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { webcrypto } from 'node:crypto';
import { installWorkspaceSample } from './workspaceApi';
import { revisionAcknowledged } from '../report/judgmentRecord';
import { sampleInstructor, WORKSPACE_SAMPLE } from './workspaceFixture';
import assets from './assets.json';
import review from './reviewData.json';

let restore: () => void;
let network: ReturnType<typeof vi.fn>;
const get = (path: string) => window.fetch(`/api${path}`);
const post = (path: string, body: object) => window.fetch(`/api${path}`, { method: 'POST', body: JSON.stringify(body) });
beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal('crypto', webcrypto);
  network = vi.fn(async (path: string) => {
    const bytes = readFileSync(resolve('public', path.slice(1)));
    return new Response(bytes, { headers: { 'Content-Type': path.endsWith('.pdf') ? 'application/pdf' : 'image/png' } });
  });
  window.fetch = network as typeof fetch;
  restore = installWorkspaceSample({ preassessed: false });
});
afterEach(() => { restore(); vi.unstubAllGlobals(); sessionStorage.clear(); });

describe('public source workspace transport', () => {
  it('opens with nine recorded rubric judgments and supports revising one with history', async () => {
    restore(); sessionStorage.clear(); restore = installWorkspaceSample();
    const report = await (await get('/check-runs/1/report')).json();
    expect(report.pending_review_count).toBe(0);
    expect(report.results.every((row: { level: unknown; resolution: unknown }) => row.level && row.resolution)).toBe(true);
    const context = await (await get('/check-runs/1/criteria/101/judgment')).json();
    const input = { resolution: 'mark_level' as const, level: 3, reason: 'Reviewed the full introduction and structure.', expected_revision: report.assessment_revision, expected_judgment_revision: 1 };
    const changed = await (await post('/check-runs/1/criteria/101/judgment/revisions', input)).json();
    expect(revisionAcknowledged(changed, 1, 101, input, context.current, sampleInstructor.id, 1101)).toBe(true);
    expect(changed.entry.before.level.name).toBe('Acceptable');
    expect(changed.context.current.level.name).toBe('Proficient');
  });
  it('records a pending level without a comment and acknowledges a later revision', async () => {
    const initial = await post('/check-runs/1/escalated/1101/resolve', { resolution: 'mark_level', level: 2, reason: '', expected_revision: 1 });
    expect(initial.status).toBe(200);
    const saved = await initial.json();
    expect(saved.report.pending_review_count).toBe(8);
    const context = await (await get('/check-runs/1/criteria/101/judgment')).json();
    expect(context.can_record).toBe(false);
    expect(context.can_revise).toBe(true);
    const input = { resolution: 'mark_level' as const, level: 3, reason: 'Reviewed the complete source and descriptor.', expected_revision: 2, expected_judgment_revision: 1 };
    const revised = await (await post('/check-runs/1/criteria/101/judgment/revisions', input)).json();
    expect(revisionAcknowledged(revised, 1, 101, input, context.current, sampleInstructor.id, 1101)).toBe(true);
    const history = await (await get('/check-runs/1/criteria/101/judgment/history')).json();
    expect(history.entries.map((entry: { action: string }) => entry.action)).toEqual(['judgment_revised', 'initial_resolution']);
    expect((await post('/check-runs/1/escalated/1102/resolve', { resolution: 'mark_level', level: 1, reason: '', expected_revision: 1 })).status).toBe(409);
  });
  it('keeps pending Library records separate and applies search', async () => {
    expect((await (await get('/library?scope=pending')).json()).total).toBe(0);
    const result = await (await get('/library?search=e-AIMSS')).json();
    expect(result.total).toBe(1);
    expect(result.items[0].manuscript_id).toBe(202);
    expect((await (await get('/library?program=Software%20Engineering')).json()).total).toBe(0);
  });
  it('refuses approval or rejection with pending criteria and preserves return/reopen reasons', async () => {
    for (const decision of ['approved', 'rejected']) expect((await post('/check-runs/1/decision', { decision, note: 'Reviewed the record.', expected_revision: 1 })).status).toBe(422);
    expect((await post('/check-runs/1/decision', { decision: 'returned', note: '', expected_revision: 1 })).status).toBe(422);
    const returned = await (await post('/check-runs/1/decision', { decision: 'returned', note: 'Complete the bibliography and implementation scope.', expected_revision: 1 })).json();
    expect(returned.pending_review_count).toBe(9);
    expect((await post('/check-runs/1/reopen', { reason: '', expected_revision: 2 })).status).toBe(422);
    const reopened = await (await post('/check-runs/1/reopen', { reason: 'Additional evidence is now available.', expected_revision: 2 })).json();
    expect(reopened.decision).toBeNull();
    expect(reopened.assessment_revision).toBe(3);
    const audit = await (await get('/audit')).json();
    expect(audit.items[0].payload.reason).toBe('Additional evidence is now available.');
    expect(audit.items[0].payload.previous_decision).toBe('returned');
  });
  it('confirms and dismisses findings with revision acknowledgments', async () => {
    const confirmed = await (await post('/flags/1/confirm-finding', { note: null, expected_revision: 1 })).json();
    expect(confirmed.finding_confirmed).toBe(true);
    expect(confirmed.report.assessment_revision).toBe(2);
    const dismissed = await (await post('/flags/1/override', { reason: 'The wording is explained in context.', expected_revision: 2 })).json();
    expect(dismissed.overridden).toBe(true);
    expect(dismissed.report.assessment_revision).toBe(3);
  });
  it('loads every allowlisted PDF with matching content and refuses an HTML fallback', async () => {
    for (const asset of assets) {
      const response = await window.fetch(asset.path);
      expect(response.status).toBe(200);
      expect((await response.arrayBuffer()).byteLength).toBe(asset.bytes);
    }
    restore(); network.mockResolvedValue(new Response('<html>SPA</html>', { headers: { 'Content-Type': 'text/html' } })); restore = installWorkspaceSample();
    expect((await get('/check-runs/1/document/file')).status).toBe(503);
  });
  it('binds evidence to the source and snapshot and rejects stale references', async () => {
    const manifest = await (await get('/check-runs/1/evidence')).json();
    const ref = review.references[0].reference_id;
    expect((await get(`/check-runs/1/evidence/${ref}/location?snapshot_id=wrong`)).status).toBe(409);
    expect((await get(`/check-runs/1/evidence/${ref}/location?snapshot_id=${manifest.snapshot_id}`)).status).toBe(200);
    const pdf = await get(`/check-runs/1/evidence/source-file?source_identity=${WORKSPACE_SAMPLE.sha256}`);
    expect(pdf.headers.get('X-Evidence-Source-Identity')).toBe(WORKSPACE_SAMPLE.sha256);
    expect((await get('/check-runs/1/evidence/source-file?source_identity=wrong')).status).toBe(409);
  });
  it('refuses unsupported writes and external requests without network traffic', async () => {
    expect((await post('/manuscripts', { file: 'private' })).status).toBe(422);
    expect((await window.fetch('https://example.com/api/auth/me')).status).toBe(403);
    expect(() => new XMLHttpRequest().open('POST', '/api/manuscripts')).toThrow();
    expect(network).not.toHaveBeenCalled();
  });
});
