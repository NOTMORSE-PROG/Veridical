// Readiness report data layer (F8.1-F8.2, screen 4h) + the escalated
// panel / resolution flow (V-023, F3.5) + the terminal decision flow
// (V-038, F8.5).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { confirmedJudgment } from "./savedJudgment";
import { captureAuthOwner as reportMutationOwner } from "../auth/queryOwner";
import { isAssessmentRevision, keepNewerAssessment } from "./assessmentRevision";
import { REPORT_READ_FRESH_MS } from "../config/network";
import type {
  Decision,
  DocumentParagraphsOut,
  EscalatedItemOut,
  EscalationResolution,
  FlagSummaryOut,
  ManuscriptViewerOut,
  ReportOut,
  ResolveEscalationOut,
  ReuseMatchesOut,
} from "../api/types";

export function useReport(checkRunId: number) {
  return useQuery({
    staleTime: REPORT_READ_FRESH_MS,
    queryKey: ["report", checkRunId],
    queryFn: () => api.get<ReportOut>(`/check-runs/${checkRunId}/report`),
    structuralSharing: (current, incoming) => keepNewerAssessment(current as ReportOut | undefined, incoming as ReportOut),
    enabled: Number.isInteger(checkRunId) && checkRunId > 0,
  });
}

export function useEscalatedItems(checkRunId: number) {
  return useQuery({
    staleTime: REPORT_READ_FRESH_MS,
    queryKey: ["report", checkRunId, "escalated"],
    queryFn: () => api.get<EscalatedItemOut[]>(`/check-runs/${checkRunId}/escalated`),
    enabled: Number.isInteger(checkRunId) && checkRunId > 0,
  });
}

// BUG-033: its own endpoint, not a field on ReportOut — same shape as
// useEscalatedItems above (a sibling panel self-fetches; the common case
// of zero flags on a run never adds dead weight to every report fetch).
export function useFlags(checkRunId: number, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    staleTime: REPORT_READ_FRESH_MS,
    queryKey: ["report", checkRunId, "flags"],
    queryFn: () => api.get<FlagSummaryOut[]>(`/check-runs/${checkRunId}/flags`),
    enabled: enabled && Number.isInteger(checkRunId) && checkRunId > 0,
  });
}

/** Observe existing complete context on a standalone detail, without fetching it. */
export function useCachedReportFlags(checkRunId: number | undefined) {
  return useQuery<FlagSummaryOut[]>({
    queryKey: ["report", checkRunId, "flags"],
    queryFn: () => api.get<FlagSummaryOut[]>(`/check-runs/${checkRunId}/flags`),
    enabled: false,
  });
}

// V-065: the manuscript viewer's metadata + per-flag anchor regions. Own
// query key (not folded into ["report", checkRunId]) -- same "sibling
// panel self-fetches" precedent as useFlags above, and this one is
// noticeably heavier (opens the PDF server-side to compute regions), so a
// screen that never opens the viewer should never pay for it.
export function useManuscriptViewer(checkRunId: number) {
  return useQuery({
    staleTime: REPORT_READ_FRESH_MS,
    queryKey: ["report", checkRunId, "document"],
    queryFn: () => api.get<ManuscriptViewerOut>(`/check-runs/${checkRunId}/document`),
    enabled: Number.isInteger(checkRunId) && checkRunId > 0,
  });
}

// V-065 AC1 (DOCX gap): the reconstructed-text pane's actual paragraph
// content. Own query key/own fetch, same "sibling panel self-fetches"
// precedent as useManuscriptViewer above -- a PDF-backed report never
// pays for this. `enabled` is the caller's job (gate on source_format
// === "docx", known only after useManuscriptViewer resolves).
export function useManuscriptParagraphs(checkRunId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["report", checkRunId, "document", "paragraphs"],
    queryFn: () =>
      api.get<DocumentParagraphsOut>(`/check-runs/${checkRunId}/document/paragraphs`),
    enabled: enabled && Number.isInteger(checkRunId) && checkRunId > 0,
  });
}

// V-072 (F7.4), design review spec §4.2/§6: the exclusion-toggle
// exploration panel's data source. Own query key, keyed by toggle state
// (so flipping a toggle is a real, separately-cached fetch, not a stale
// re-render) -- never touches ["report", checkRunId, "flags"], since
// nothing this returns is ever a scored Flag (see the backend's own
// module docstring, app/checks/reuse/query.py). Disabled while both
// toggles are off: nothing to show, no wasted round trip.
// Always enabled, not gated on either toggle being on: `passage_archive_size_n`
// (the exploration panel's always-shown coverage disclosure, ticket AC5)
// comes from this SAME endpoint regardless of toggle state, and real HNSW
// query latency at this archive's projected worst-case row count measured
// ~1-2ms (V-072.md's own research) -- cheap enough that gating the fetch
// buys no real savings and would leave the disclosure with nothing to show
// until a toggle is flipped.
export function useExcludedReuseMatches(
  checkRunId: number,
  { includeReferenceList, includeBlockQuote }: { includeReferenceList: boolean; includeBlockQuote: boolean },
) {
  return useQuery({
    queryKey: ["report", checkRunId, "reuse-matches", includeReferenceList, includeBlockQuote],
    queryFn: () => {
      const params = new URLSearchParams({
        include_reference_list: String(includeReferenceList),
        include_block_quote: String(includeBlockQuote),
      });
      return api.get<ReuseMatchesOut>(`/check-runs/${checkRunId}/document/reuse-matches?${params}`);
    },
    enabled: Number.isInteger(checkRunId) && checkRunId > 0,
  });
}

const RESOLUTION_OUTCOMES = new Set(["passed", "failed", "not_applicable"]);

function resolutionResponseMatches(out: ResolveEscalationOut, checkRunId: number, checkResultId: number) {
  return out !== null && typeof out === "object" && out.check_result_id === checkResultId
    && RESOLUTION_OUTCOMES.has(out.outcome) && out.report !== null && typeof out.report === "object"
    && out.report.check_run_id === checkRunId && Array.isArray(out.report.results)
    && out.report.results.every((row) => row !== null && typeof row === "object" && typeof row.criterion_id === "number"
      && (row.level == null || (typeof row.level === "object" && typeof row.level.name === "string")));
}

export function useResolveEscalation(checkRunId: number, isCurrent: () => boolean) {
  const queryClient = useQueryClient();
  function refreshResolutionRecords() {
    if (!isCurrent()) return;
    void queryClient.invalidateQueries({ queryKey: ["report", checkRunId], exact: true });
    void queryClient.invalidateQueries({ queryKey: ["report", checkRunId, "escalated"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  }
  return useMutation({
    mutationFn: async ({
      checkResultId,
      criterionId,
      resolution,
      reason,
      level,
      expectedRevision,
    }: {
      checkResultId: number;
      criterionId: number;
      resolution: EscalationResolution;
      reason: string;
      // V-069 AC4: required (and only meaningful) when resolution is
      // "mark_level" -- the level's own ordinal.
      level?: number;
      expectedRevision: number;
    }) => {
      if (!isAssessmentRevision(expectedRevision)) throw new ApiError(409, "conflict", "Review the current criterion before confirming.");
      const out = await api.post<ResolveEscalationOut>(
        `/check-runs/${checkRunId}/escalated/${checkResultId}/resolve`,
        { resolution, reason, level, expected_revision: expectedRevision },
      );
      if (!resolutionResponseMatches(out, checkRunId, checkResultId)
        || !isAssessmentRevision(expectedRevision) || out.report.assessment_revision !== expectedRevision + 1
        || !confirmedJudgment(out, criterionId, { resolution, reason, level })) {
        throw new ApiError(502, "invalid_response", "The server response did not confirm this resolution. Refresh the review before confirming again.");
      }
      return out;
    },
    // Concurrent sibling replies can contain older report snapshots. Refetch
    // the authoritative report rather than installing the last-arriving reply.
    onSuccess: (out) => {
      if (!isCurrent()) return;
      queryClient.setQueryData<ReportOut>(["report", checkRunId], (current) => keepNewerAssessment(current, out.report));
      refreshResolutionRecords();
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.code === "invalid_response" || error.status === 409)) refreshResolutionRecords();
    },
  });
}

// A decided/reopened report can go stale from a genuine race (a second
// tab, or clicking decide right after the last pending item was
// resolved elsewhere) -- the 409 the server sends back is itself correct
// and already well-worded (charter rule 3), but the LOCAL cache is now
// wrong until the next fetch. Re-fetching on exactly that error code
// means closing the modal shows the real current state instead of
// silently re-offering an action that will fail again.
function refetchReportOnConflict(queryClient: ReturnType<typeof useQueryClient>, checkRunId: number) {
  return (error: unknown) => {
    if (error instanceof ApiError && (error.status === 409 || error.code === "invalid_response")) {
      queryClient.invalidateQueries({ queryKey: ["report", checkRunId] });
    }
  };
}

export function useDecideReport(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => reportMutationOwner(queryClient),
    mutationFn: async (body: { decision: Decision; note: string | null; expected_revision: number }) => {
      if (!isAssessmentRevision(body.expected_revision)) throw new ApiError(409, "conflict", "Reload the current assessment before confirming.");
      const out = await api.post<ReportOut>(`/check-runs/${checkRunId}/decision`, body);
      if (!out || typeof out !== "object" || !isAssessmentRevision(body.expected_revision) || out.check_run_id !== checkRunId
        || out.assessment_revision !== body.expected_revision + 1 || out.decision !== body.decision
        || out.decision_note !== body.note) {
        throw new ApiError(502, "invalid_response", "The response did not confirm this decision. Reload the report before trying again.");
      }
      return out;
    },
    onSuccess: (report, _body, owner) => {
      if (!owner?.isCurrent() || report.check_run_id !== checkRunId) return;
      queryClient.setQueryData<ReportOut>(["report", checkRunId], (current) => keepNewerAssessment(current, report));
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (error, _input, owner) => { if (owner?.isCurrent()) refetchReportOnConflict(queryClient, checkRunId)(error); },
  });
}

export function useReopenReport(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => reportMutationOwner(queryClient),
    mutationFn: async (body: { reason: string; expected_revision: number }) => {
      if (!isAssessmentRevision(body.expected_revision)) throw new ApiError(409, "conflict", "Reload the current assessment before reopening.");
      const out = await api.post<ReportOut>(`/check-runs/${checkRunId}/reopen`, body);
      if (!out || typeof out !== "object" || !isAssessmentRevision(body.expected_revision) || out.check_run_id !== checkRunId
        || out.assessment_revision !== body.expected_revision + 1 || out.decision !== null) {
        throw new ApiError(502, "invalid_response", "The response did not confirm reopening. Reload the report before trying again.");
      }
      return out;
    },
    onSuccess: (report, _reason, owner) => {
      if (!owner?.isCurrent() || report.check_run_id !== checkRunId) return;
      queryClient.setQueryData<ReportOut>(["report", checkRunId], (current) => keepNewerAssessment(current, report));
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (error, _input, owner) => { if (owner?.isCurrent()) refetchReportOnConflict(queryClient, checkRunId)(error); },
  });
}

// V-039: fetches the PDF as a Blob and triggers a real file save via a
// temporary object URL -- a plain `<a href>` can't show an in-page
// aria-busy/error state while the export generates (real, if small,
// latency: the ticket's own AC is "<30s"), and can't distinguish a
// failed export from a successful one without a page navigation.
export function useExportReportPdf(checkRunId: number) {
  const queryClient = useQueryClient();
  const createRequest = () => ({ runId: checkRunId, owner: reportMutationOwner(queryClient),
    revision: queryClient.getQueryData<ReportOut>(["report", checkRunId])?.assessment_revision });
  const mutation = useMutation({
    mutationFn: async (request: ReturnType<typeof createRequest>) => {
      const requireOwner = () => {
        if (!request.owner.isCurrent() || !Number.isInteger(request.runId) || request.runId <= 0) {
          throw new ApiError(401, "unauthorized", "Sign in again before exporting this report.");
        }
      };
      requireOwner();
      const base = `/check-runs/${request.runId}/report`;
      let revision = request.revision;
      if (!isAssessmentRevision(revision)) {
        const report = await api.get<ReportOut>(base);
        requireOwner();
        if (report.check_run_id !== request.runId || !isAssessmentRevision(report.assessment_revision)) {
          throw new ApiError(502, "invalid_response", "Reload the report before exporting.");
        }
        revision = report.assessment_revision;
      }
      type Job = { job_id: string; check_run_id: number; assessment_revision: number;
        state: string; message: string | null; poll_seconds: number; wait_seconds: number };
      const validate = (job: Job, id?: string) => {
        if (!job || !/^[a-f0-9]{48}$/.test(job.job_id) || (id && job.job_id !== id)
          || job.check_run_id !== request.runId || job.assessment_revision !== revision
          || !["preparing", "ready", "failed", "stale"].includes(job.state)
          || !Number.isFinite(job.poll_seconds) || job.poll_seconds <= 0
          || !Number.isFinite(job.wait_seconds) || job.wait_seconds <= 0) {
          throw new ApiError(502, "invalid_response", "PDF preparation could not be confirmed. Export again.");
        }
        return job;
      };
      let job = validate(await api.post<Job>(`${base}/export-jobs`, { expected_revision: revision }));
      const id = job.job_id;
      const deadline = performance.now() + job.wait_seconds * 1000;
      const interval = job.poll_seconds * 1000;
      while (job.state === "preparing") {
        requireOwner();
        if (performance.now() >= deadline) {
          throw new ApiError(408, "export_wait", "The PDF is still preparing. Export again to check its progress.");
        }
        await new Promise(resolve => window.setTimeout(resolve, interval));
        requireOwner();
        job = validate(await api.get<Job>(`${base}/export-jobs/${id}`), id);
      }
      requireOwner();
      if (job.state !== "ready") {
        throw new ApiError(409, "export_failed", job.message || "PDF preparation could not finish. Export again.");
      }
      return api.getBlob(`${base}/export-jobs/${id}/file`);
    },
    onSuccess: (blob, request) => {
      // A response can outlive its observer. Recheck the invoking auth lifetime
      // before any DOM delivery; ordinary same-owner navigation is allowed.
      if (!request.owner.isCurrent()) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `veridical-report-${request.runId}.pdf`;
      try {
        document.body.appendChild(link);
        link.click();
      } finally {
        link.remove();
        URL.revokeObjectURL(url);
      }
    },
  });
  return { ...mutation, createRequest };
}
