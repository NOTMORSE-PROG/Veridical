// Readiness report data layer (F8.1-F8.2, screen 4h) + the escalated
// panel / resolution flow (V-023, F3.5) + the terminal decision flow
// (V-038, F8.5).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type {
  Decision,
  EscalatedItemOut,
  EscalationResolution,
  FlagSummaryOut,
  ReportOut,
  ResolveEscalationOut,
} from "../api/types";

export function useReport(checkRunId: number) {
  return useQuery({
    queryKey: ["report", checkRunId],
    queryFn: () => api.get<ReportOut>(`/check-runs/${checkRunId}/report`),
  });
}

export function useEscalatedItems(checkRunId: number) {
  return useQuery({
    queryKey: ["report", checkRunId, "escalated"],
    queryFn: () => api.get<EscalatedItemOut[]>(`/check-runs/${checkRunId}/escalated`),
  });
}

// BUG-033: its own endpoint, not a field on ReportOut — same shape as
// useEscalatedItems above (a sibling panel self-fetches; the common case
// of zero flags on a run never adds dead weight to every report fetch).
export function useFlags(checkRunId: number) {
  return useQuery({
    queryKey: ["report", checkRunId, "flags"],
    queryFn: () => api.get<FlagSummaryOut[]>(`/check-runs/${checkRunId}/flags`),
  });
}

export function useResolveEscalation(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      checkResultId,
      resolution,
      reason,
    }: {
      checkResultId: number;
      resolution: EscalationResolution;
      reason: string;
    }) =>
      api.post<ResolveEscalationOut>(
        `/check-runs/${checkRunId}/escalated/${checkResultId}/resolve`,
        { resolution, reason },
      ),
    onSuccess: (out) => {
      // The response already carries the fresh report (ticket AC: "live");
      // seed both caches directly instead of an extra round-trip.
      queryClient.setQueryData(["report", checkRunId], out.report);
      queryClient.invalidateQueries({ queryKey: ["report", checkRunId, "escalated"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] }); // dashboard badge
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
    if (error instanceof ApiError && error.code === "conflict") {
      queryClient.invalidateQueries({ queryKey: ["report", checkRunId] });
    }
  };
}

export function useDecideReport(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { decision: Decision; note: string | null }) =>
      api.post<ReportOut>(`/check-runs/${checkRunId}/decision`, body),
    onSuccess: (report) => {
      queryClient.setQueryData(["report", checkRunId], report);
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: refetchReportOnConflict(queryClient, checkRunId),
  });
}

export function useReopenReport(checkRunId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) => api.post<ReportOut>(`/check-runs/${checkRunId}/reopen`, { reason }),
    onSuccess: (report) => {
      queryClient.setQueryData(["report", checkRunId], report);
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: refetchReportOnConflict(queryClient, checkRunId),
  });
}

// V-039: fetches the PDF as a Blob and triggers a real file save via a
// temporary object URL -- a plain `<a href>` can't show an in-page
// aria-busy/error state while the export generates (real, if small,
// latency: the ticket's own AC is "<30s"), and can't distinguish a
// failed export from a successful one without a page navigation.
export function useExportReportPdf(checkRunId: number) {
  return useMutation({
    mutationFn: () => api.getBlob(`/check-runs/${checkRunId}/report/export.pdf`),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `veridical-report-${checkRunId}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    },
  });
}
