// Readiness report data layer (F8.1-F8.2, screen 4h) + the escalated
// panel / resolution flow (V-023, F3.5).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  EscalatedItemOut,
  EscalationResolution,
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
