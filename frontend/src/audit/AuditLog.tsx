// Screen 4s — Audit log (F8.10): filterable, immutable, every AI call and
// instructor action traceable in a few clicks (ticket AC). The detail
// drawer shows the FULL stored payload (prompt/response/context) — never
// truncated, only the on-screen preview is capped (ticket edge case).
import { useSearchParams } from "react-router";
import type { AuditLogSummary } from "../api/types";
import { Modal, ModalBackdrop } from "../components/Modal";
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
  flag_override: "Flag overridden",
  flag_annotated: "Flag annotated",
};

function eventLabel(row: AuditLogSummary): string {
  return EVENT_LABELS[row.event_type] ?? row.event_type;
}

function eventSummary(row: AuditLogSummary): string {
  const parts: string[] = [];
  if (row.prompt_type) parts.push(row.prompt_type);
  if (row.prompt_version) parts.push(`prompt v${row.prompt_version.replace(/^v/, "")}`);
  if (row.agreement_score !== null) {
    const asFraction = Math.round(row.agreement_score * 3);
    parts.push(`agreement ${row.agreement_score.toFixed(2)} (~${asFraction}/3)`);
  }
  return parts.join(" · ");
}

function AuditDetailModal({ id, onClose }: { id: number; onClose: () => void }) {
  const { data, isLoading } = useAuditLogDetail(id);
  const payloadPreview = data ? JSON.stringify(data.payload, null, 2) : "";
  const capped = payloadPreview.length > 4000;

  return (
    <ModalBackdrop>
      <Modal title={`Audit entry #${id}`} onClose={onClose}>
        {isLoading && <p className="text-xs text-ink-faint">Loading…</p>}
        {data && (
          <div className="flex flex-col gap-2 text-xs">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-ink-soft">
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
              <span>
                <b className="text-ink">When:</b> {new Date(data.created_at).toLocaleString()}
              </span>
            </div>
            {data.input_hash && (
              <div className="rounded-control bg-page px-2.5 py-1.5 font-mono text-2xs break-all text-ink-faint">
                input_hash: {data.input_hash}
              </div>
            )}
            <div>
              <div className="mb-1 text-2xs font-semibold tracking-header text-table-head uppercase">
                Raw payload {capped && "(preview — full record is stored, never truncated)"}
              </div>
              <pre className="max-h-80 overflow-auto rounded-control border border-border-soft bg-page p-2.5 text-2xs whitespace-pre-wrap text-ink">
                {capped ? payloadPreview.slice(0, 4000) + "\n…" : payloadPreview}
              </pre>
            </div>
            <p className="rounded-control bg-info-bg px-2.5 py-1.5 text-2xs text-info-text">
              Immutable — this entry cannot be edited or deleted. Replay it exactly with{" "}
              <code>uv run python -m scripts.replay_call {id}</code>.
            </p>
          </div>
        )}
      </Modal>
    </ModalBackdrop>
  );
}

export function AuditLogPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Number(searchParams.get("page") ?? "1");
  const checkRunIdParam = searchParams.get("check_run_id");
  const checkRunId = checkRunIdParam ? Number(checkRunIdParam) : undefined;
  const activeFilterLabel = searchParams.get("filter") ?? "All";
  const detailId = searchParams.get("detail") ? Number(searchParams.get("detail")) : null;

  const activeFilter = FILTERS.find((f) => f.label === activeFilterLabel) ?? FILTERS[0];
  const { data, isLoading } = useAuditLogPage(
    {
      checkRunId,
      eventType: activeFilter.eventType,
      eventTypePrefix: activeFilter.eventTypePrefix,
    },
    page,
  );

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null) next.delete(key);
    else next.set(key, value);
    if (key !== "page") next.delete("page");
    setSearchParams(next);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="border-b border-border-soft pb-2">
        <div className="text-lg font-bold text-ink">Audit log</div>
        <div className="text-xs text-ink-faint">
          Every AI call, escalation resolution, and override — append-only.
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            onClick={() => setParam("filter", f.label === "All" ? null : f.label)}
            className={
              activeFilterLabel === f.label
                ? "rounded-pill bg-primary px-2.5 py-0.5 text-xs font-semibold text-on-primary"
                : "rounded-pill border border-modal-border bg-panel px-2.5 py-0.5 text-xs text-ink-soft"
            }
          >
            {f.label}
          </button>
        ))}
        {checkRunId !== undefined && (
          <>
            <span className="flex-1" />
            <span className="rounded-pill border border-modal-border bg-panel px-2.5 py-0.5 text-xs text-ink-soft">
              check run {checkRunId}
            </span>
            <button
              type="button"
              onClick={() => setParam("check_run_id", null)}
              className="text-xs text-primary hover:underline"
            >
              Clear
            </button>
          </>
        )}
      </div>

      <div className="rounded-panel border border-border">
        <div className="grid grid-cols-[130px_1fr_90px] gap-2.5 border-b border-border-soft bg-table-head-bg px-3.5 py-2 text-2xs font-semibold tracking-header text-table-head uppercase">
          <span>Time</span>
          <span>Event</span>
          <span />
        </div>
        {isLoading && <p className="px-3.5 py-3 text-xs text-ink-faint">Loading…</p>}
        {data?.items.map((row) => (
          <div
            key={row.id}
            className="grid grid-cols-[130px_1fr_90px] items-center gap-2.5 border-t border-border-soft px-3.5 py-2 text-xs"
          >
            <span className="text-ink-faint">{new Date(row.created_at).toLocaleString()}</span>
            <span className="text-ink">
              <b>{eventLabel(row)}</b>
              {eventSummary(row) && <span className="text-ink-soft"> — {eventSummary(row)}</span>}
              {row.manuscript_group_label && (
                <span className="text-ink-faint"> · {row.manuscript_group_label}</span>
              )}
            </span>
            <button
              type="button"
              onClick={() => setParam("detail", String(row.id))}
              className="justify-self-start text-primary hover:underline"
            >
              Detail
            </button>
          </div>
        ))}
        {data && data.items.length === 0 && (
          <p className="px-3.5 py-3 text-xs text-ink-faint">No audit events match this filter.</p>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-end gap-2 text-xs">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setParam("page", String(page - 1))}
            className="rounded-control border border-border-button bg-panel px-2.5 py-1 disabled:opacity-45"
          >
            Previous
          </button>
          <span className="text-ink-faint">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setParam("page", String(page + 1))}
            className="rounded-control border border-border-button bg-panel px-2.5 py-1 disabled:opacity-45"
          >
            Next
          </button>
        </div>
      )}

      {detailId !== null && (
        <AuditDetailModal id={detailId} onClose={() => setParam("detail", null)} />
      )}
    </div>
  );
}
