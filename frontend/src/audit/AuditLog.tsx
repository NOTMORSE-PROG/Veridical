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
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ApiError } from "../api/client";
import type { AuditLogSummary } from "../api/types";
import { Chip } from "../components/Chip";
import { Modal, ModalBackdrop } from "../components/Modal";
import { useRouteFocus } from "../routing/useRouteFocus";
import { useAuditLogDetail, useAuditLogPage } from "./useAudit";

const FILTERS: ReadonlyArray<{ label: string; eventType?: string; eventTypePrefix?: string }> = [
  { label: "All" },
  { label: "AI calls", eventTypePrefix: "llm_" },
  { label: "Routing", eventType: "criterion_routing" },
  { label: "Escalations", eventType: "escalation_resolved" },
  { label: "Overrides", eventTypePrefix: "flag_" },
];

const EVENT_LABELS: Record<string, string> = {
  llm_call: "AI call",
  llm_cache_hit: "AI call (cached)",
  llm_call_failed: "AI call failed",
  criterion_routing: "Criterion routing",
  escalation_resolved: "Escalation resolved",
  flag_overridden: "Flag overridden",
  flag_annotated: "Flag annotated",
  rubric_parse_attempt: "Rubric parse attempt",
  llm_model_exhausted: "AI model exhausted, failed over",
  report_decided: "Decision recorded",
  report_reopened: "Decision reopened",
  manuscript_purged: "Archive purged",
  check_run_cancel_requested: "Check cancellation requested",
  check_run_cancelled: "Check cancelled",
  citation_source_confirmed: "Citation source confirmed",
  manuscript_ingestion_failure_dismissed: "Failed upload moved to Archive",
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
  llm_call: "VERIDICAL completed an AI-assisted review step and preserved the exact inputs and response for reproducibility.",
  llm_cache_hit: "VERIDICAL reused a matching stored AI response instead of spending another service call.",
  llm_call_failed: "An AI-assisted review step did not complete. The record preserves the failure context without treating it as an assessment.",
  criterion_routing: "VERIDICAL recorded whether a criterion could be checked by a deterministic rule or needed AI-assisted review.",
  escalation_resolved: "You resolved a criterion that VERIDICAL had left for instructor judgment.",
  flag_overridden: "You changed how a system finding should affect the readiness review.",
  flag_annotated: "You added instructor context to a system finding.",
  rubric_parse_attempt: "VERIDICAL recorded an attempt to prepare review criteria from the uploaded required format.",
  llm_model_exhausted: "The configured AI service choices were unavailable, so the affected work was not treated as assessed.",
  report_decided: "You recorded the final instructor outcome for this readiness review.",
  report_reopened: "You reopened a previously recorded instructor outcome for further review.",
  manuscript_purged: "You permanently removed archived manuscript content while retaining the minimum audit history.",
  check_run_cancel_requested: "You requested that an active manuscript check stop at its next safe boundary.",
  check_run_cancelled: "VERIDICAL stopped the manuscript check and preserved its completed work and history.",
  citation_source_confirmed: "You confirmed the source associated with a citation-integrity finding.",
  manuscript_ingestion_failure_dismissed: "You moved a failed upload out of the active Review Desk and into Archive.",
};

function humanizeIdentifier(value: string): string {
  const words = value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (!words) return "Recorded activity";
  return words[0].toUpperCase() + words.slice(1);
}

function eventLabel(row: AuditLogSummary): string {
  return EVENT_LABELS[row.event_type] ?? humanizeIdentifier(row.event_type);
}

function eventExplanation(row: AuditLogSummary): string {
  return EVENT_EXPLANATIONS[row.event_type]
    ?? "VERIDICAL preserved this activity as part of the review's immutable history.";
}

function eventSummary(row: AuditLogSummary): string {
  const parts: string[] = [];
  if (row.prompt_type) parts.push(PROMPT_LABELS[row.prompt_type] ?? humanizeIdentifier(row.prompt_type));
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
  | { kind: "group"; eventType: string; label: string; groupLabel: string | null; rows: AuditLogSummary[] };

function groupConsecutive(items: AuditLogSummary[]): AuditRowGroup[] {
  const out: AuditRowGroup[] = [];
  for (const row of items) {
    const canGroup = !isInstructorEvent(row.event_type);
    const last = out[out.length - 1];
    if (
      canGroup &&
      last?.kind === "group" &&
      last.eventType === row.event_type &&
      last.groupLabel === (row.manuscript_group_label ?? null)
    ) {
      last.rows.push(row);
      continue;
    }
    if (canGroup) {
      out.push({
        kind: "group",
        eventType: row.event_type,
        label: eventLabel(row),
        groupLabel: row.manuscript_group_label ?? null,
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

function isInstructorEvent(eventType: string): boolean {
  return (
    eventType === "flag_overridden" ||
    eventType === "flag_annotated" ||
    eventType === "escalation_resolved" ||
    eventType === "report_decided" ||
    eventType === "report_reopened" ||
    eventType === "manuscript_purged" ||
    eventType === "check_run_cancel_requested" ||
    eventType === "citation_source_confirmed" ||
    eventType === "manuscript_ingestion_failure_dismissed"
  );
}

function formatEventTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

function ActorIcon({ instructor }: { instructor: boolean }) {
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
  return instructor ? (
    <svg {...common}>
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
    </svg>
  ) : (
    <svg {...common}>
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function EventContent({ row }: { row: AuditLogSummary }) {
  const instructor = isInstructorEvent(row.event_type);
  const summary = eventSummary(row);
  return (
    <>
      <ActorIcon instructor={instructor} />{" "}
      <span className="sr-only">{instructor ? "Instructor action: " : "Automated: "}</span>
      <b>{eventLabel(row)}</b>
      {summary && <span className="text-ink-secondary"> · {summary}</span>}
      {row.manuscript_group_label && (
        <span className="text-ink-tertiary"> · {row.manuscript_group_label}</span>
      )}
    </>
  );
}

function AuditDetailModal({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, isLoading, isError, error, refetch } = useAuditLogDetail(id);
  const payloadPreview = data ? JSON.stringify(data.payload, null, 2) : "";
  const instructor = data ? isInstructorEvent(data.event_type) : false;
  const instructorId =
    data && typeof data.payload.instructor_id === "number" ? data.payload.instructor_id : null;

  return (
    <ModalBackdrop>
      <Modal title={`Audit entry #${id}`} onClose={onClose}>
        {isLoading && (
          <p role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
            Loading audit entry.
          </p>
        )}
        {isError && (
          <div role="alert" className="text-sm text-status-attention-text">
            {error instanceof ApiError ? error.message : "This audit entry couldn't be loaded."}{" "}
            <button type="button" onClick={() => refetch()} className="font-medium underline">
              Try again
            </button>
            .
          </div>
        )}
        {data && (
          <div className="flex flex-col gap-3 text-sm">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-ink-secondary">
              <span>
                <b className="text-ink">Event:</b> {eventLabel(data)}
              </span>
              {data.check_run_id !== null && (
                <span>
                  <b className="text-ink">Check run:</b> {data.check_run_id}
                </span>
              )}
              {data.manuscript_group_label && (
                <span>
                  <b className="text-ink">Group:</b> {data.manuscript_group_label}
                </span>
              )}
              {data.manuscript_original_filename && (
                <span>
                  <b className="text-ink">File:</b> {data.manuscript_original_filename}
                </span>
              )}
              {instructor && instructorId !== null && (
                <span>
                  <b className="text-ink">Performed by:</b> You
                </span>
              )}
              <span>
                <b className="text-ink">When:</b>{" "}
                {new Date(data.created_at).toLocaleString(undefined, {
                  dateStyle: "medium",
                  timeStyle: "medium",
                })}
              </span>
            </div>
            <section aria-labelledby={`audit-entry-${id}-meaning`} className="rounded-md border border-border bg-page p-3">
              <h3 id={`audit-entry-${id}-meaning`} className="font-semibold text-ink">What this records</h3>
              <p className="mt-1 text-ink-secondary">{eventExplanation(data)}</p>
              {eventSummary(data) && (
                <p className="mt-1 text-ink-secondary">Review context: {eventSummary(data)}.</p>
              )}
            </section>
            <p className="rounded-md bg-status-info-bg px-2.5 py-1.5 text-xs text-status-info-text">
              This entry is immutable: it cannot be edited or deleted. Its exact stored values
              remain available below for technical inspection and reproduction workflows, but
              they are not required for the instructor's review task.
            </p>
            <details className="rounded-md border border-border bg-panel p-3">
              <summary className="min-h-11 cursor-pointer font-semibold text-link">
                Technical record for reproducibility
              </summary>
              <p className="mb-2 text-xs text-ink-secondary">
                These are the exact stored system values. Field names and identifiers are shown
                unchanged so a technical reviewer can inspect the record or use it in a
                reproduction workflow.
              </p>
              {data.agreement_score !== null && (
                <div className="rounded-md bg-page px-2.5 py-1.5 font-mono text-xs break-all text-ink-tertiary">
                  agreement_score: {data.agreement_score}
                </div>
              )}
              {data.input_hash && (
                <div className="mt-2 rounded-md bg-page px-2.5 py-1.5 font-mono text-xs break-all text-ink-tertiary">
                  input_hash: {data.input_hash}
                </div>
              )}
              <div className="mt-2">
                <div className="mb-1 text-xs font-semibold tracking-header text-ink-tertiary uppercase">
                  Raw payload
                </div>
                <pre className="max-h-80 overflow-auto rounded-md border border-border bg-page p-2.5 text-xs whitespace-pre-wrap break-words text-ink">
                  {payloadPreview}
                </pre>
              </div>
            </details>
          </div>
        )}
      </Modal>
    </ModalBackdrop>
  );
}

export function AuditLogPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Audit log - VERIDICAL", headingRef);

  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number(searchParams.get("page") ?? "1");
  const checkRunIdParam = searchParams.get("check_run_id");
  const checkRunId = checkRunIdParam ? Number(checkRunIdParam) : undefined;
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

  const activeFilter = FILTERS.find((f) => f.label === activeFilterLabel) ?? FILTERS[0];
  const { data, isLoading, isError, refetch } = useAuditLogPage(
    {
      checkRunId,
      eventType: activeFilter.eventType,
      eventTypePrefix: activeFilter.eventTypePrefix,
    },
    page,
  );

  useEffect(() => {
    if (!data) return;
    const scope = checkRunId !== undefined ? ` for check run ${checkRunId}` : "";
    setAnnouncement(
      data.items.length === 0
        ? `No audit events match this filter${scope}.`
        : `Showing ${data.items.length} of ${data.total} event${data.total === 1 ? "" : "s"}${scope}.`,
    );
  }, [data, checkRunId]);

  // Previous/Next disables itself on whichever page edge it lands on
  // (page 1 or the last page) — the browser drops focus to <body> when a
  // focused control becomes disabled, with no native recovery. Moving
  // focus to the page indicator keeps a keyboard/screen-reader instructor
  // anchored somewhere real instead of stranded (found live, ux-critic).
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
    if (key !== "page") next.delete("page");
    setSearchParams(next);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="signal-route signal-page-flow signal-audit">
      <header className="signal-route-header">
        <div>
          <p className="signal-eyebrow">Immutable activity record</p>
        <h1
          ref={headingRef}
          id="audit-log-heading"
          tabIndex={-1}
        >
          Audit
        </h1>
        <p className="signal-route-header__intro">
          Trace system activity and instructor actions. Audit entries cannot be edited or deleted.
        </p>
        </div>
      </header>

      {checkRunId !== undefined && (
        <aside className="signal-audit-context" aria-label="Audit scope">
          <div>
            <strong>Showing activity for check run {checkRunId}</strong>
            <span>This filtered record follows the selected readiness review.</span>
          </div>
          <Link to={`/report/${checkRunId}`}>Return to readiness report</Link>
        </aside>
      )}

      <div className="signal-section-flow signal-audit-workspace">
        <p role="status" aria-live="polite" className="sr-only">
          {announcement}
        </p>

      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter by event type" className="flex flex-wrap items-center gap-1.5">
          {FILTERS.map((f) => {
            const isActive = activeFilterLabel === f.label;
            return (
              <button
                key={f.label}
                type="button"
                aria-pressed={isActive}
                onClick={() => setParam("filter", f.label === "All" ? null : f.label)}
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
          {/* Mobile: card-per-row (grouped rows collapse to one card). */}
          <ul aria-labelledby="audit-log-heading" className="flex flex-col gap-2 sm:hidden">
            {data &&
              groupConsecutive(data.items).map((entry) => {
                if (entry.kind === "single") {
                  const row = entry.row;
                  return (
                    <li key={row.id} className="rounded-lg border border-border bg-panel p-3">
                      <div className="flex items-start justify-between gap-2">
                        <span className="text-xs text-ink-tertiary">{formatEventTime(row.created_at)}</span>
                        <button
                          type="button"
                          onClick={() => setParam("detail", String(row.id))}
                          className="inline-flex min-h-11 items-center rounded-md border border-border-input px-3 text-sm font-medium text-link hover:bg-status-neutral-bg"
                        >
                          Detail
                        </button>
                      </div>
                      <div className="mt-1.5 text-sm text-ink">
                        <EventContent row={row} />
                      </div>
                    </li>
                  );
                }
                const firstId = entry.rows[0].id;
                const expanded = expandedGroups.has(firstId);
                return (
                  <li key={firstId} className="rounded-lg border border-border bg-panel p-3">
                    <button
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => toggleGroup(firstId)}
                      className="flex min-h-11 w-full items-center justify-between gap-2 text-left text-sm text-ink"
                    >
                      <span>
                        <b>{entry.rows.length}</b> x {entry.label} events between{" "}
                        {formatEventTime(entry.rows[entry.rows.length - 1].created_at)} and{" "}
                        {formatEventTime(entry.rows[0].created_at)}
                        {entry.groupLabel && <span className="text-ink-tertiary"> · {entry.groupLabel}</span>}
                      </span>
                      <span aria-hidden="true" className="flex-none text-ink-tertiary">
                        {expanded ? "−" : "+"}
                      </span>
                    </button>
                    {expanded && (
                      <ul className="mt-2 flex flex-col gap-2 border-t border-border pt-2">
                        {entry.rows.map((row) => (
                          <li key={row.id} className="flex items-start justify-between gap-2">
                            <span className="text-xs text-ink-tertiary">{formatEventTime(row.created_at)}</span>
                            <button
                              type="button"
                              onClick={() => setParam("detail", String(row.id))}
                              className="inline-flex min-h-11 items-center rounded-md border border-border-input px-3 text-sm font-medium text-link hover:bg-status-neutral-bg"
                            >
                              Detail
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                );
              })}
            {data && data.items.length === 0 && (
              <li className="rounded-lg border border-border bg-panel p-4 text-sm text-ink-tertiary">
                No audit events match this filter.
              </li>
            )}
          </ul>

          {/* Desktop: the grid table. */}
          <div
            role="table"
            aria-labelledby="audit-log-heading"
            className="hidden overflow-hidden rounded-lg border border-border sm:block"
          >
            <div
              role="row"
              className="grid grid-cols-[152px_minmax(0,1fr)_84px] gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase"
            >
              <span role="columnheader">Time</span>
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
                        {formatEventTime(row.created_at)}
                      </span>
                      <span role="cell" className="min-w-0 text-ink">
                        <EventContent row={row} />
                      </span>
                      <span role="cell" className="justify-self-end">
                        <button
                          type="button"
                          onClick={() => setParam("detail", String(row.id))}
                          className="inline-flex min-h-8 items-center text-sm font-medium text-link underline hover:text-link-hover"
                        >
                          Detail
                        </button>
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
                        {formatEventTime(entry.rows[entry.rows.length - 1].created_at)} to{" "}
                        {formatEventTime(entry.rows[0].created_at)}
                      </span>
                      <span role="cell" className="min-w-0 text-ink">
                        <b>{entry.rows.length}</b> x {entry.label} events
                        {entry.groupLabel && <span className="text-ink-tertiary"> · {entry.groupLabel}</span>}
                      </span>
                      <span role="cell" className="justify-self-end">
                        <button
                          type="button"
                          aria-expanded={expanded}
                          onClick={() => toggleGroup(firstId)}
                          className="inline-flex min-h-8 items-center text-sm font-medium text-link underline hover:text-link-hover"
                        >
                          {expanded ? "Hide" : "Show all"}
                        </button>
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
                            {formatEventTime(row.created_at)}
                          </span>
                          <span role="cell" className="min-w-0 text-ink">
                            <EventContent row={row} />
                          </span>
                          <span role="cell" className="justify-self-end">
                            <button
                              type="button"
                              onClick={() => setParam("detail", String(row.id))}
                              className="inline-flex min-h-8 items-center text-sm font-medium text-link underline hover:text-link-hover"
                            >
                              Detail
                            </button>
                          </span>
                        </div>
                      ))}
                  </div>
                );
              })}
            {data && data.items.length === 0 && (
              <p role="cell" className="px-4 py-3 text-sm text-ink-tertiary">
                No audit events match this filter.
              </p>
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
        <AuditDetailModal id={detailId} onClose={() => setParam("detail", null)} />
      )}
    </div>
  );
}
