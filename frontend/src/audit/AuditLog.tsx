// Screen 4s — Audit log (F8.10): filterable, immutable, every AI call and
// instructor action traceable in a few clicks (ticket AC). The detail
// drawer shows the FULL stored payload (prompt/response/context) — never
// truncated, only the on-screen preview is capped (ticket edge case).
//
// V-055 rebuild: this is VERIDICAL's reproducibility surface, the one
// screen a hostile examiner is most likely to be shown directly (the
// panel-is-the-adversary heuristic). Fixed: filter pills had no
// aria-pressed (a screen reader couldn't tell which was active), the
// desktop grid's bare `1fr` track broke down to 91px at 375px (same
// failure class as BUG-021), Detail buttons measured 18px tall (WCAG
// 2.5.8), no route focus management existed on this screen at all, and
// filter/page changes gave no live-region announcement. Also surfaces a
// human-action-vs-automated-call distinction (icon + sr-only text, never
// color alone) using data the backend already sends but this screen
// never rendered.
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import type { AuditLogSummary, LLMMode } from "../api/types";
import { Chip } from "../components/Chip";
import { useActionLifetime } from "../auth/useActionLifetime";
import { Dialog } from "../ui/Dialog";
import { recoverableReadError } from "../report/reportReadState";
import { instructorEventDetails, recordedActor } from "./instructorEventDetails";
import { auditEventOrigin, type AuditEventOrigin } from "./eventOrigin";
import { rememberRouteReturnFocus, useRouteFocus } from "../routing/useRouteFocus";
import { readAuditReviewOrigin } from "../routing/auditReturn";
import { auditOwnerCurrent, useAuditLogDetail, useAuditLogPage, useAuditOwner, type AuditOwner } from "./useAudit";
import { Button } from "../ui/Button";
import { CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";

const FILTERS: ReadonlyArray<{ label: string; value?: string; eventType?: string; eventTypePrefix?: string }> = [
  { label: "All" },
  { label: "AI calls", eventTypePrefix: "llm_" },
  { label: "Routing", eventType: "criterion_routing" },
  { label: "Judgments on pending criteria", value: "Escalations", eventType: "escalation_resolved" },
  { label: "Judgments on assessed criteria", eventType: "judgment_recorded" },
  { label: "Judgment revisions", eventType: "judgment_revised" },
  { label: "Overrides", eventTypePrefix: "flag_" },
];

const EVENT_LABELS: Record<string, string> = {
  llm_call: "AI call",
  llm_transport_attempt: "AI request attempt",
  llm_cache_hit: "AI call (cached)",
  llm_call_failed: "AI call failed",
  criterion_routing: "Criterion routing",
  escalation_resolved: "Criterion judgment recorded",
  judgment_revised: "Criterion judgment revised",
  judgment_recorded: "Criterion judgment recorded",
  manuscript_group_selected: "Saved group assigned",
  flag_overridden: "Flag overridden",
  flag_annotated: "Flag annotated",
  rubric_parse_attempt: "Rubric parse attempt",
  llm_model_exhausted: "Model limit reached",
  report_decided: "Decision recorded",
  report_reopened: "Decision reopened",
  manuscript_purged: "Stored file deleted",
  check_run_cancel_requested: "Check cancellation requested",
  check_run_cancelled: "Check cancelled",
  citation_source_confirmed: "Citation source confirmed",
  manuscript_ingestion_failure_dismissed: "Failed upload removed from Review Desk",
  library_entry_added: "Manuscript added to the Library",
  library_entry_needs_decision: "Possible duplicate held for your decision",
  library_entry_failed: "Library file could not be read",
  library_entry_edited: "Library record details edited",
  library_entry_removed: "Manuscript removed from the Library",
  library_orphan_corpus_rows_removed: "Stray comparison data removed",
};

const PROMPT_LABELS: Record<string, string> = {
  agreement_pairing: "Grading agreement review",
  claim_support: "Claim and citation review",
  image_table_extraction: "Image and table reading",
  ping: "AI service check",
  rubric_decomposition: "Required-format preparation",
  semantic_grading: "Semantic criterion review",
};

const EVENT_EXPLANATIONS: Record<string, string> = {
  llm_call: "VERIDICAL completed an automated review step. Open the details to inspect the information that was saved.",
  llm_cache_hit: "VERIDICAL reused a matching stored AI response instead of spending another service call.",
  llm_call_failed: "An AI-assisted review step did not complete. The record preserves the failure context without treating it as an assessment.",
  criterion_routing: "VERIDICAL recorded whether a criterion could be checked by a deterministic rule or needed AI-assisted review.",
  escalation_resolved: "An instructor resolved a criterion that VERIDICAL had left for instructor judgment.",
  judgment_revised: "An instructor revised a previously recorded criterion judgment. The earlier judgment remains in the review history.",
  judgment_recorded: "An instructor recorded their first judgment on a criterion that already had an automated assessment. The original assessment is kept in the review history.",
  manuscript_group_selected: "An instructor explicitly selected a saved group for this manuscript. The saved group record was retained.",
  flag_overridden: "An instructor changed how a system finding should affect the readiness review.",
  flag_annotated: "An instructor added context to a system finding.",
  rubric_parse_attempt: "VERIDICAL recorded an attempt to prepare review criteria from the uploaded required format.",
  llm_model_exhausted: "A review attempt reached a recorded model or service limit. Check later entries and the report for what happened next.",
  report_decided: "An instructor recorded the final outcome for this readiness review.",
  report_reopened: "An instructor reopened a previously recorded outcome for further review.",
  manuscript_purged: "An instructor deleted the uploaded file and its extracted text. The manuscript cannot be used as a source in future originality checks. Saved reports, decisions, comparison evidence and audit history are kept.",
  check_run_cancel_requested: "An instructor requested that an active manuscript check stop at its next safe boundary.",
  check_run_cancelled: "VERIDICAL stopped the manuscript check and preserved its completed work and history.",
  citation_source_confirmed: "An instructor confirmed the source associated with a citation-integrity finding.",
  manuscript_ingestion_failure_dismissed: "An instructor removed a failed upload from the active Review Desk. Its record and history are kept.",
  library_entry_added: "A manuscript became a Library record, so originality checks now compare against it.",
  library_entry_needs_decision: "An uploaded file closely matched a recorded Library item and was held for an instructor decision instead of being added.",
  library_entry_failed: "VERIDICAL could not read text from an uploaded file, so it was not added to the Library.",
  library_entry_edited: "An instructor changed a Library record's details.",
  library_entry_removed: "A manuscript left the Library, so originality checks no longer compare against it.",
  library_orphan_corpus_rows_removed: "An operator cleanup removed comparison data that belonged to no Library record.",
};

// Mode-specific summaries describe the recorded event, not a final run outcome.
// Missing mode does not establish record age or a complete replay package.
const EVENT_LABELS_BY_MODE: Partial<Record<string, Partial<Record<LLMMode, string>>>> = {
  llm_call: { fake: "Test-mode call", unknown: "Automated review step" },
  llm_cache_hit: { fake: "Test-mode call (cached)", unknown: "Automated review step (cached)" },
  llm_call_failed: { fake: "Test-mode call failed", unknown: "Automated review step failed" },
};

const EVENT_EXPLANATIONS_BY_MODE: Partial<Record<string, Partial<Record<LLMMode, string>>>> = {
  llm_call: {
    real: "This entry records a completed AI-assisted review step using a real external AI model. Open the details to inspect the information that was saved.",
    fake: "This entry records a completed test-mode review step using simulated fixture responses. Do not treat the simulated result as a real AI assessment. Open the details to inspect the information that was saved.",
    unknown: "This entry records a completed automated review step. It does not establish whether the responses came from a real external model or simulated fixtures. Open the details to inspect the information that was saved.",
  },
  llm_cache_hit: {
    real: "VERIDICAL reused a matching stored response from a real external AI model instead of spending another service call.",
    fake: "VERIDICAL reused a matching stored response from its test-mode AI fixture instead of spending another service call. The reused response is simulated, not a real model's output, consistent with VERIDICAL's zero-budget test-mode default.",
    unknown: "This entry records reuse of a stored response. It does not establish whether that response came from a real external model or a simulated fixture. No new generation is recorded by this event.",
  },
  llm_call_failed: {
    real: "An AI-assisted review step using a real external AI model did not complete. The record preserves the failure context without treating it as an assessment.",
    fake: "An automated test-mode review step using VERIDICAL's own fixture responses did not complete. The record preserves the failure context without treating it as an assessment. A failure in test mode does not indicate a problem with a real AI service.",
    unknown: "This entry records an automated review attempt that did not complete. Its AI response mode could not be verified. Check later entries and the report for the recorded assessment outcome.",
  },
  llm_model_exhausted: {
    unknown: "A review attempt reached a recorded model or service limit. This entry does not establish the AI response mode or the check’s final outcome. Check later entries and the report for what happened next.",
  },
};

function humanizeIdentifier(value: string): string {
  // Check names read the way the rest of the product names them
  // ("statistical_forensics_..." becomes "Statistical checks ...").
  const kind = Object.keys(CHECK_KIND_SHORT_LABEL).find((key) => value.startsWith(`${key}_`));
  const named = kind ? `${CHECK_KIND_SHORT_LABEL[kind]} ${value.slice(kind.length + 1).replace(/[_-]+/g, " ")}`.replace(/\bchecks check\b/i, "checks") : value;
  const words = named.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (!words) return "Recorded activity";
  return words[0].toUpperCase() + words.slice(1);
}

function eventLabel(row: AuditLogSummary): string {
  const modes = Object.hasOwn(EVENT_LABELS_BY_MODE, row.event_type) ? EVENT_LABELS_BY_MODE[row.event_type] : undefined;
  const byMode = row.llm_execution_mode && modes && Object.hasOwn(modes, row.llm_execution_mode) ? modes[row.llm_execution_mode] : undefined;
  return byMode ?? (Object.hasOwn(EVENT_LABELS, row.event_type) ? EVENT_LABELS[row.event_type] : humanizeIdentifier(row.event_type));
}

function eventExplanation(row: AuditLogSummary): string {
  const modes = Object.hasOwn(EVENT_EXPLANATIONS_BY_MODE, row.event_type) ? EVENT_EXPLANATIONS_BY_MODE[row.event_type] : undefined;
  const byMode = row.llm_execution_mode && modes && Object.hasOwn(modes, row.llm_execution_mode) ? modes[row.llm_execution_mode] : undefined;
  return byMode
    ?? (Object.hasOwn(EVENT_EXPLANATIONS, row.event_type) ? EVENT_EXPLANATIONS[row.event_type] : undefined)
    ?? "VERIDICAL preserved this activity as part of the review's immutable history.";
}

// BUG-219: a compact, per-row/per-group companion to `eventLabel` — never
// fused into the label itself (that would multiply 4 event labels x 3
// modes into a combinatorial set of strings, and would leave "real" with
// no positive confirmation anywhere, only silence). Reuses the exact color
// pair already shipped for this same fact on the report/flag/adviser
// screens (`signal-warning`: design review traced its contrast — 7.02:1 —
// and confirmed it already cleared the palette's protanopia/deuteranopia
// gate as part of that rollout) at a scale suited to a routine, possibly
// per-row log rather than a page-level decision gate: full `Alert` banners
// once per row would themselves overclaim urgency on a routine, documented,
// zero-budget-by-design default. Referenced via Tailwind's arbitrary-value
// syntax rather than a `bg-signal-warning-bg` utility name, because this
// token pair is only ever consumed today through `signal.css`'s own
// hand-written classes (`.signal-alert--warning`, etc.), not through a
// generated Tailwind utility — this pulls the same token values without
// depending on a class name nothing in this file's styling system has ever
// emitted.
//
// UX review finding (P2): DESIGN.md's own color-vision rule for this
// EXACT semantic pair (`System warning`) requires a non-color cue ("warning
// triangle") on top of the text, not just the text itself — a first draft
// of this comment justified text-alone by analogy to plain, non-semantic
// metadata (`prompt v3`), which doesn't carry a color-coded state at all
// and was the wrong comparison. `/report/14`'s own `Alert`/`ModeDisclosure`
// already renders this identical fact with a shape cue (a colored left
// rail); this tag was the one place that fact went out uncued. Fixed with
// a small triangle glyph, not a full rail, since a page-level Alert's
// border treatment doesn't scale down to an inline pill.
function ExecutionModeTag({ mode }: { mode: LLMMode | null }) {
  if (mode !== "fake" && mode !== "unknown") return null;
  return (
    // UX review finding (P3): every other inline segment in this row
    // (eventSummary, manuscript_group_label) is preceded by " · ", giving a
    // screen reader an audible pause between segments -- this tag had none,
    // running "AI call Test mode" together with no separator.
    <>
      <span className="text-ink-tertiary"> · </span>
      <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-signal-warning-bg)] px-2 py-0.5 text-xs font-semibold text-[color:var(--color-signal-warning-text)]">
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          width="11"
          height="11"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="shrink-0"
        >
          <path d="M12 3.5 21.5 20.5 2.5 20.5Z" />
          <path d="M12 9.5v4.5" />
          <circle cx="12" cy="17.3" r="0.75" fill="currentColor" stroke="none" />
        </svg>
        {mode === "fake" ? "Test mode" : "Mode unknown"}
      </span>
    </>
  );
}

function eventSummary(row: AuditLogSummary): string {
  const parts: string[] = [];
  if (row.prompt_type) parts.push(Object.hasOwn(PROMPT_LABELS, row.prompt_type) ? PROMPT_LABELS[row.prompt_type] : humanizeIdentifier(row.prompt_type));
  if (row.prompt_version) parts.push(`prompt v${row.prompt_version.replace(/^v/, "")}`);
  // `agreement_score` is deliberately absent from the instructor summary.
  // The audit contract does not carry its denominator or provenance: a
  // semantic score can come from two passes (a third runs only after a
  // disagreement), while instructor override events reuse the same field
  // for flag confidence. Turning it into a vote count would invent facts.
  // The exact stored number remains available in the technical disclosure.
  return parts.join(" · ");
}

// Grouping (V-056) — a real screen showed five consecutive "Criterion
// routing · Ungrouped" rows in an 11-minute span, each rendered at full
// visual cost: the worst Miller's-law/data-ink-ratio case in the app.
// Purely presentational, over the CURRENT page's already-fetched rows
// only — no pagination/backend change, so a group never spans a page
// boundary and a filter/page change can't silently hide a row. Only
// non-instructor (automated) events group; overrides/escalations/manual
// actions always stay full-weight, one row each (higher-stakes, low-
// volume, exactly the rows that should NOT recede).
type AuditRowGroup =
  | { kind: "single"; row: AuditLogSummary }
  | {
      kind: "group";
      eventType: string;
      checkRunId: number | null;
      label: string;
      groupLabel: string | null;
      executionMode: LLMMode | null;
      rows: AuditLogSummary[];
    };

function groupConsecutive(items: AuditLogSummary[]): AuditRowGroup[] {
  const out: AuditRowGroup[] = [];
  for (const row of items) {
    const canGroup = auditEventOrigin(row.event_type) === "automated";
    const last = out[out.length - 1];
    if (
      canGroup &&
      last?.kind === "group" &&
      last.eventType === row.event_type &&
      last.checkRunId === row.check_run_id &&
      last.groupLabel === (row.manuscript_group_label ?? null) &&
      // BUG-219: a fixture-mode run and a real-model run must never
      // collapse into one summary row that can only state one label — this
      // is what keeps a grouped batch from ever needing a separate "mixed"
      // indicator, rather than adding one.
      last.executionMode === row.llm_execution_mode
    ) {
      last.rows.push(row);
      continue;
    }
    if (canGroup) {
      out.push({
        kind: "group",
        eventType: row.event_type,
        checkRunId: row.check_run_id,
        label: eventLabel(row),
        groupLabel: row.manuscript_group_label ?? null,
        executionMode: row.llm_execution_mode,
        rows: [row],
      });
    } else {
      out.push({ kind: "single", row });
    }
  }
  // A "group" of exactly one row is just a single row with extra steps —
  // flatten it back rather than rendering a pointless expand toggle.
  return out.map((g) => (g.kind === "group" && g.rows.length === 1 ? { kind: "single", row: g.rows[0] } : g));
}

function formatEventTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

function ActorIcon({ origin }: { origin: AuditEventOrigin }) {
  const common = {
    "aria-hidden": true as const,
    viewBox: "0 0 24 24",
    width: 14,
    height: 14,
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: "-mt-0.5 inline-block shrink-0 text-ink-tertiary",
  };
  return origin === "instructor" ? (
    <svg {...common}>
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
    </svg>
  ) : origin === "automated" ? (
    <svg {...common}>
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  ) : <svg {...common}><path d="M7 3h7l4 4v14H6V3Z" /><path d="M13 3v5h5M9 12h6M9 16h6" /></svg>;
}

function EventContent({ row }: { row: AuditLogSummary }) {
  const origin = auditEventOrigin(row.event_type);
  const summary = eventSummary(row);
  return (
    <>
      <ActorIcon origin={origin} />{" "}
      <span className="sr-only">{origin === "instructor" ? "Instructor action: " : origin === "automated" ? "Automated: " : "Recorded activity: "}</span>
      <b>{eventLabel(row)}</b>
      {origin === "unknown" && <span className="text-ink-secondary"> · Origin unavailable</span>}
      <ExecutionModeTag mode={row.llm_execution_mode} />
      {summary && <span className="text-ink-secondary"> · {summary}</span>}
      {row.manuscript_group_label && (
        <span className="text-ink-tertiary"> · {row.manuscript_group_label}</span>
      )}
    </>
  );
}

function AuditDetailModal({ id, owner, onClose, fallbackFocusRef }: {
  id: number; owner: AuditOwner; onClose: () => void;
  fallbackFocusRef: RefObject<HTMLHeadingElement | null>;
}) {
  const query = useAuditLogDetail(id, owner);
  const client = useQueryClient();
  const isCurrent = useActionLifetime(`audit-detail:${id}`);
  const data = auditOwnerCurrent(client, owner) && query.data?.id === id
    && (!query.isError || recoverableReadError(query.error)) ? query.data : undefined;
  const payloadPreview = data ? JSON.stringify(data.payload, null, 2) : "";
  const details = data ? instructorEventDetails(data.event_type, data.payload, data) : undefined;
  function close() { if (isCurrent()) onClose(); }
  return (
    <Dialog title={`Audit entry #${id}`} initialFocusTitle onClose={close} fallbackFocusRef={fallbackFocusRef} wide>
      {query.isPending && !data && <p role="status" aria-busy="true">Loading audit entry.</p>}
      {query.isError && <div role="alert" className="signal-copy-flow">
        <p>{query.error instanceof ApiError ? query.error.message : "This audit entry could not be loaded."}</p>
        <button type="button" onClick={event => {
          if (!isCurrent()) return;
          // Retire the disappearing control before the read starts. Completion
          // never claims focus from a subsequent choice or a different dialog.
          if (document.activeElement === event.currentTarget) {
            event.currentTarget.closest('[role="dialog"]')?.querySelector<HTMLHeadingElement>("h2")?.focus();
          }
          void query.refetch();
        }} className="signal-button signal-button--secondary">Retry audit entry</button>
      </div>}
      {data && <div className="signal-group-flow">
        {query.isError && <p role="status">Last loaded entry. Its current availability could not be confirmed.</p>}
        <section aria-labelledby={`audit-entry-${id}-meaning`} className="signal-audit-entry-summary signal-group-flow">
          <h3 id={`audit-entry-${id}-meaning`}>{details?.heading ?? "What this records"}</h3>
          {details ? <>
            <dl className="signal-audit-entry-facts">
              {details.fields.map(field => <div key={field.label} className={`signal-audit-entry-fact--${field.role}`}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
            </dl>
            {details.notice && <p>{details.notice}</p>}
            {details.pendingItems && details.pendingItems.length > 0 && <details>
              <summary>View recorded pending items</summary>
              <ul>{details.pendingItems.map(item => <li key={item.resultId}>Criterion #{item.criterionId}, result #{item.resultId}: {item.outcome}</li>)}</ul>
            </details>}
            {details.pendingQualification && <p>{details.pendingQualification}</p>}
          </> : <>
            <p>{eventExplanation(data)}</p>
            {eventSummary(data) && <p>Review context: {eventSummary(data)}.</p>}
          </>}
        </section>
        <section aria-labelledby={`audit-entry-${id}-context`} className="signal-audit-entry-context signal-group-flow">
          <h3 id={`audit-entry-${id}-context`}>Entry context</h3>
          <dl className="signal-audit-entry-facts">
            {!details && <div><dt>Event</dt><dd>{eventLabel(data)}</dd></div>}
            <div><dt>Stored time</dt><dd>{formatEventTime(data.created_at)}</dd></div>
            {data.check_run_id !== null && <div><dt>Check run</dt><dd>{data.check_run_id}</dd></div>}
            {auditEventOrigin(data.event_type) === "instructor" && <div><dt>Performed by</dt><dd>{recordedActor(data.payload, owner.instructorId, data)}</dd></div>}
            {auditEventOrigin(data.event_type) === "unknown" && <div><dt>Action origin</dt><dd>Not established by this entry.</dd></div>}
            {data.manuscript_group_label && <div className="signal-audit-entry-fact--primary"><dt>Group</dt><dd>{data.manuscript_group_label}</dd></div>}
            {data.manuscript_original_filename && <div className="signal-audit-entry-fact--primary"><dt>File</dt><dd>{data.manuscript_original_filename}</dd></div>}
          </dl>
        </section>
        <p>This stored entry is read-only.</p>
        <details className="signal-audit-technical">
          <summary>Technical record for reproducibility</summary>
          <div className="signal-copy-flow">
            <p>These are the exact stored system values. Field names and identifiers remain unchanged so a technical reviewer can inspect the record or use it in a reproduction workflow.</p>
            <p>VERIDICAL does not allow this entry to be edited or deleted. A database trigger rejects UPDATE and DELETE while enabled. Privileged database access can bypass or disable this safeguard.</p>
            <p>Entry numbers identify records, not verified event times or transaction completion order. Older records may use different system clocks.</p>
            <p>Stored timestamp: <code>{data.created_at}</code></p>
            <p>Event type: <code>{data.event_type}</code></p>
            {data.agreement_score !== null && <p><code>agreement_score: {data.agreement_score}</code></p>}
            {data.input_hash && <p><code>input_hash: {data.input_hash}</code></p>}
            {data.event_type.startsWith("llm_") && <div>
              <p>Requested model: {typeof data.payload?.requested_model === "string" ? data.payload.requested_model : typeof data.payload?.model === "string" ? data.payload.model : "Not recorded"}</p>
              <p>{data.event_type === "llm_cache_hit" ? "Provider-reported model version of cached response" : "Provider-reported model version"}: {data.llm_execution_mode === "fake" ? "Not applicable: test-mode response" : typeof data.payload?.model_version === "string" && data.payload.model_version.trim() ? data.payload.model_version : "Not recorded"}</p>
              {data.event_type === "llm_cache_hit" && <p>This entry reused a stored response. No new generation is recorded by this event.</p>}
            </div>}
            <div><strong>Raw payload</strong><pre>{payloadPreview}</pre></div>
          </div>
        </details>
      </div>}
    </Dialog>
  );
}

export function AuditLogPage() {
  const { owner, access } = useAuditOwner();
  if (!owner) return <div className="signal-route signal-page-flow signal-audit">
    <h1>Audit</h1>
    {access.isError ? <div role="alert"><p>Instructor access could not be confirmed. Private audit content is hidden.</p><button type="button" onClick={() => { void access.refetch(); }}>Retry instructor access</button></div>
      : <p role="status">Checking instructor access.</p>}
  </div>;
  return <OwnedAuditLogPage key={`${owner.instructorId}:${owner.lifetime}`} owner={owner} />;
}

function OwnedAuditLogPage({ owner }: { owner: AuditOwner }) {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number(searchParams.get("page") ?? "1");
  const checkRunIdParam = searchParams.get("check_run_id");
  const checkRunId = checkRunIdParam ? Number(checkRunIdParam) : undefined;
  const origin = checkRunId === undefined ? undefined : readAuditReviewOrigin(location.search, checkRunId);
  const originPath = origin?.focusPath;
  const originAction = origin?.elementId;
  const originFallback = origin?.fallbackElementId;
  const originScrollBlock = origin?.scrollBlock;
  useLayoutEffect(() => {
    if (originPath && originAction) rememberRouteReturnFocus(originPath, location.pathname + location.search, originAction,
      { armed: true, fallbackElementId: originFallback, scrollBlock: originScrollBlock });
  }, [originPath, originAction, originFallback, originScrollBlock, location.pathname, location.search]);
  useRouteFocus("Audit log - VERIDICAL", headingRef);
  const activeFilterLabel = searchParams.get("filter") ?? "All";
  const detailId = searchParams.get("detail") ? Number(searchParams.get("detail")) : null;
  const [announcement, setAnnouncement] = useState("");
  const [expandedGroups, setExpandedGroups] = useState<Set<number>>(new Set());

  function toggleGroup(firstId: number) {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(firstId)) next.delete(firstId);
      else next.add(firstId);
      return next;
    });
  }

  const activeFilter = FILTERS.find((f) => (f.value ?? f.label) === activeFilterLabel) ?? FILTERS[0];
  const listQuery = useAuditLogPage(
    {
      checkRunId,
      eventType: activeFilter.eventType,
      eventTypePrefix: activeFilter.eventTypePrefix,
    },
    page,
    owner,
  );
  const { isLoading, isError, refetch, isPlaceholderData } = listQuery;
  const data = auditOwnerCurrent(useQueryClient(), owner)
    && (!listQuery.isError || recoverableReadError(listQuery.error)) ? listQuery.data : undefined;

  useEffect(() => {
    if (!data) return;
    const scope = checkRunId !== undefined ? ` for check run ${checkRunId}` : "";
    setAnnouncement(
      data.items.length === 0
        ? activeFilter.label === "All" && checkRunId === undefined
          ? "No system activity or instructor actions have been recorded yet."
          : `No audit events match this filter${scope}.`
        : `Showing ${data.items.length} of ${data.total} event${data.total === 1 ? "" : "s"}${scope}.`,
    );
  }, [data, checkRunId, activeFilter.label]);

  // Previous/Next disables itself on whichever page edge it lands on
  // (page 1 or the last page) — the browser drops focus to <body> when a
  // focused control becomes disabled, with no native recovery. Moving
  // focus to the page indicator keeps a keyboard/screen-reader instructor
  // anchored somewhere real instead of stranded (found live, UX review).
  const pageIndicatorRef = useRef<HTMLSpanElement>(null);
  const prevPageRef = useRef(page);
  useEffect(() => {
    if (prevPageRef.current !== page) {
      pageIndicatorRef.current?.focus();
    }
    prevPageRef.current = page;
  }, [page]);

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null) next.delete(key);
    else next.set(key, value);
    if (key !== "page" && key !== "detail") next.delete("page");
    if (key === "filter") next.delete("detail");
    setSearchParams(next);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="signal-route signal-page-flow signal-audit">
      <header className="signal-route-header">
        <div>
          <p className="signal-eyebrow">Activity record</p>
        <h1
          ref={headingRef}
          id="audit-log-heading"
          tabIndex={-1}
        >
          Audit
        </h1>
        <p className="signal-route-header__intro">
          Trace system activity and instructor actions. VERIDICAL does not allow an audit entry to
          be edited or deleted.
        </p>
        </div>
      </header>

      {checkRunId !== undefined && (
        <aside className="signal-audit-context" aria-label="Audit scope">
          <div>
            <strong>Showing activity for check run {checkRunId}</strong>
            <span>This filtered record follows the selected readiness review.</span>
          </div>
          <Link to={origin?.returnPath ?? `/report/${checkRunId}`}>{origin?.returnPath.includes("/document") ? "Return to manuscript review" : "Return to readiness report"}</Link>
        </aside>
      )}

      <div className="signal-section-flow signal-audit-workspace">
        <p role="status" aria-live="polite" className="sr-only">
          {announcement}
        </p>

      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter by event type" className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map((f) => {
            const isActive = activeFilter === f;
            return (
              <button
                key={f.label}
                type="button"
                aria-pressed={isActive}
                onClick={() => setParam("filter", f.label === "All" ? null : (f.value ?? f.label))}
                className={
                  isActive
                    ? "on-dark inline-flex items-center gap-1 rounded-full bg-action px-3 py-1.5 text-xs font-semibold text-on-action"
                    : "inline-flex items-center rounded-full border border-border-input bg-panel px-3 py-1.5 text-xs text-ink-secondary hover:bg-status-neutral-bg"
                }
              >
                {isActive && (
                  <svg
                    aria-hidden="true"
                    viewBox="0 0 24 24"
                    width="12"
                    height="12"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M6 12.5l3.5 3.5L18 7" />
                  </svg>
                )}
                {f.label}
              </button>
            );
          })}
        </div>
        {checkRunId !== undefined && (
          <div className="flex items-center gap-1.5">
            <Chip>check run {checkRunId}</Chip>
            <button
              type="button"
              onClick={() => setParam("check_run_id", null)}
              className="inline-flex min-h-6 items-center rounded-md px-1.5 text-xs font-medium text-link underline hover:text-link-hover"
            >
              Clear
            </button>
          </div>
        )}
      </div>

      <div className="signal-group-flow signal-audit-records">
      <p className="signal-filter-help">
        {isPlaceholderData && <span role="status">Loading page {page}. The entries below are from the last loaded page. </span>}
        Order: Highest entry number first. Stored times are shown unchanged and may not follow this order.
      </p>
      {isLoading && (
        <div
          role="status"
          aria-live="polite"
          aria-busy="true"
          className="rounded-lg border border-border bg-panel p-4 text-sm text-ink-tertiary"
        >
          Loading audit log.
        </div>
      )}

      {isError && (
        <div
          role="alert"
          className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text"
        >
          Could not load the audit log.{" "}
          <button type="button" onClick={() => refetch()} className="font-medium underline">
            Try again
          </button>
          .
        </div>
      )}

      {!isLoading && !isError && (
        <>
          <div
            role="table"
            aria-labelledby="audit-log-heading"
            className="overflow-auto rounded-lg border border-border"
          >
            <div
              role="row"
              className="grid grid-cols-[152px_minmax(0,1fr)_84px] gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase"
            >
              <span role="columnheader">Entry / stored time</span>
              <span role="columnheader">Event</span>
              <span role="columnheader">
                <span className="sr-only">Actions</span>
              </span>
            </div>
            {data &&
              groupConsecutive(data.items).map((entry) => {
                if (entry.kind === "single") {
                  const row = entry.row;
                  return (
                    <div
                      key={row.id}
                      role="row"
                      className="grid grid-cols-[152px_minmax(0,1fr)_84px] items-start gap-3 border-t border-border px-4 py-3 text-sm"
                    >
                      <span role="cell" className="pt-0.5 text-xs text-ink-tertiary">
                        Entry #{row.id}<br />
                        {formatEventTime(row.created_at)}
                      </span>
                      <span role="cell" className="min-w-0 text-ink">
                        <EventContent row={row} />
                      </span>
                      <span role="cell" className="justify-self-end">
                        <Button variant="secondary" className="signal-audit-detail" onClick={() => setParam("detail", String(row.id))}
                          aria-label={`View details for entry #${row.id}`}>
                          View details
                        </Button>
                      </span>
                    </div>
                  );
                }
                const firstId = entry.rows[0].id;
                const expanded = expandedGroups.has(firstId);
                return (
                  <div key={firstId}>
                    <div
                      role="row"
                      className="grid grid-cols-[152px_minmax(0,1fr)_84px] items-start gap-3 border-t border-border bg-page px-4 py-2 text-sm"
                    >
                      <span role="cell" className="pt-0.5 text-xs text-ink-tertiary">
                        Grouped entries
                      </span>
                      <span role="cell" className="min-w-0 text-ink">
                        <b>{entry.rows.length}</b> x {entry.label} events
                        {entry.groupLabel && <span className="text-ink-tertiary"> · {entry.groupLabel}</span>}
                        <ExecutionModeTag mode={entry.executionMode} />
                      </span>
                      <span role="cell" className="justify-self-end">
                        <Button variant="quiet" className="signal-audit-detail" aria-expanded={expanded} onClick={() => toggleGroup(firstId)}
                          aria-label={`${expanded ? "Hide" : "Show all"} ${entry.rows.length} ${entry.label} events`}>
                          {expanded ? "Hide" : "Show all"}
                        </Button>
                      </span>
                    </div>
                    {expanded &&
                      entry.rows.map((row) => (
                        <div
                          key={row.id}
                          role="row"
                          className="grid grid-cols-[152px_minmax(0,1fr)_84px] items-start gap-3 border-t border-border py-2 pr-4 pl-8 text-sm"
                        >
                          <span role="cell" className="pt-0.5 text-xs text-ink-tertiary">
                            Entry #{row.id}<br />
                            {formatEventTime(row.created_at)}
                          </span>
                          <span role="cell" className="min-w-0 text-ink">
                            <EventContent row={row} />
                          </span>
                          <span role="cell" className="justify-self-end">
                            <Button variant="secondary" className="signal-audit-detail" onClick={() => setParam("detail", String(row.id))}
                              aria-label={`View details for entry #${row.id}`}>
                              View details
                            </Button>
                          </span>
                        </div>
                      ))}
                  </div>
                );
              })}
            {data && data.items.length === 0 && (
              <div role="row"><p role="cell" aria-colspan={3} className="px-4 py-3 text-sm text-ink-tertiary">
                {activeFilter.label === "All" && checkRunId === undefined
                  ? "No system activity or instructor actions have been recorded yet."
                  : "No audit events match this filter."}
              </p></div>
            )}
          </div>
        </>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-sm">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setParam("page", String(page - 1))}
            className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3 disabled:opacity-45 sm:h-8"
          >
            Previous
          </button>
          <span ref={pageIndicatorRef} tabIndex={-1} className="text-ink-tertiary">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setParam("page", String(page + 1))}
            className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-3 disabled:opacity-45 sm:h-8"
          >
            Next
          </button>
        </div>
      )}
      </div>
      </div>

      {detailId !== null && (
        <AuditDetailModal key={detailId} id={detailId} owner={owner} onClose={() => setParam("detail", null)} fallbackFocusRef={headingRef} />
      )}
    </div>
  );
}
