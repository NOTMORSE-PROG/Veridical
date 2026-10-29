// V-089: confirm or dismiss many findings of one check at once. Each finding
// changes exactly as its own confirm or dismiss would; findings already in
// that state are left unchanged and listed back.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { FlagSummaryOut, ReportOut } from "../api/types";
import { isAssessmentRevision, keepNewerAssessment } from "./assessmentRevision";

export type BulkAction = "confirm" | "dismiss";

export interface BulkResolveInput {
  action: BulkAction;
  flag_ids: number[];
  expected_revision: number;
  note?: string | null;
  reason?: string | null;
  unopened_count?: number;
}

export interface BulkResolveOut {
  flags: FlagSummaryOut[];
  unchanged_ids: number[];
  report: ReportOut;
  batch_id: string | null;
}

const INVALID = "The response did not confirm this change. Review the current findings before trying again.";

function validOut(out: unknown, input: BulkResolveInput, checkRunId: number): out is BulkResolveOut {
  if (!out || typeof out !== "object") return false;
  const value = out as Partial<BulkResolveOut>;
  const sent = new Set(input.flag_ids);
  return Array.isArray(value.flags) && Array.isArray(value.unchanged_ids)
    && value.flags.every((flag) => sent.has(flag?.id))
    && value.unchanged_ids.every((id) => sent.has(id))
    && value.report?.check_run_id === checkRunId && isAssessmentRevision(value.report.assessment_revision)
    && value.report.assessment_revision >= input.expected_revision;
}

export function useBulkResolveFlags(checkRunId: number) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["report", checkRunId] });
    void queryClient.invalidateQueries({ queryKey: ["flag"] });
    void queryClient.invalidateQueries({ queryKey: ["stats"] });
  };
  return useMutation({
    mutationFn: async (input: BulkResolveInput) => {
      if (!isAssessmentRevision(input.expected_revision)) throw new ApiError(409, "conflict", "Review the current assessment before changing these findings.");
      const out = await api.post<unknown>(`/check-runs/${checkRunId}/flags/bulk-resolve`, input);
      if (!validOut(out, input, checkRunId)) throw new ApiError(502, "invalid_response", INVALID);
      return out;
    },
    onSuccess: (out) => {
      queryClient.setQueryData<ReportOut>(["report", checkRunId], (current) => keepNewerAssessment(current, out.report));
      refresh();
    },
    // A refusal changes nothing, but the screen may be behind: reload what it shows.
    onError: (error) => { if (error instanceof ApiError && error.status === 409) refresh(); },
  });
}
