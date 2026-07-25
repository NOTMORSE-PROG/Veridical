// Readiness report data layer (F8.1-F8.2, screen 4h).
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ReportOut } from "../api/types";

export function useReport(checkRunId: number) {
  return useQuery({
    queryKey: ["report", checkRunId],
    queryFn: () => api.get<ReportOut>(`/check-runs/${checkRunId}/report`),
  });
}
