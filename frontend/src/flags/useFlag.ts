// Flag evidence/annotation/override data layer (F8.2/F8.4, screen 4i).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { ConfirmCitationSourceOut, FlagOut, OverrideFlagOut, ReportOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { isAssessmentRevision, keepNewerAssessment } from "../report/assessmentRevision";

function flagMutationOwner(client: ReturnType<typeof useQueryClient>, flagId: number) {
  const authQuery = client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true });
  const instructorId = client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id;
  const runId = client.getQueryData<FlagOut>(["flag", flagId])?.check_run_id;
  return { accepts: (out: FlagOut) => authQuery !== undefined && instructorId !== undefined
    && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === authQuery
    && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === instructorId
    && out?.id === flagId && runId !== undefined && out.check_run_id === runId };
}

export function useFlag(flagId: number) {
  return useQuery({
    queryKey: ["flag", flagId],
    queryFn: () => api.get<FlagOut>(`/flags/${flagId}`),
    structuralSharing: (current, incoming) => keepNewerAssessment(current as FlagOut | undefined, incoming as FlagOut),
    enabled: Number.isInteger(flagId) && flagId > 0,
  });
}

export function useAnnotateFlag(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => flagMutationOwner(queryClient, flagId),
    mutationFn: (annotation: string) =>
      api.post<FlagOut>(`/flags/${flagId}/annotate`, { annotation }),
    onSuccess: (out, _input, owner) => {
      if (owner?.accepts(out)) queryClient.setQueryData<FlagOut>(["flag", flagId], current => keepNewerAssessment(current, out));
    },
  });
}

export function useOverrideFlag(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => flagMutationOwner(queryClient, flagId),
    mutationFn: async (body: { reason: string; expected_revision: number }) => {
      if (!isAssessmentRevision(body.expected_revision)) throw new ApiError(409, "conflict", "Review the current assessment before overriding.");
      const out = await api.post<OverrideFlagOut>(`/flags/${flagId}/override`, body);
      if (!out || typeof out !== "object" || out.id !== flagId || !out.overridden || out.override_reason !== body.reason
        || out.assessment_revision !== body.expected_revision + 1 || out.report?.check_run_id !== out.check_run_id
        || out.report.assessment_revision !== out.assessment_revision) {
        throw new ApiError(502, "invalid_response", "The response did not confirm this override. Review the current finding before trying again.");
      }
      return out;
    },
    onSuccess: (out, _input, owner) => {
      if (!owner?.accepts(out) || out.report?.check_run_id !== out.check_run_id) return;
      queryClient.setQueryData<FlagOut>(["flag", flagId], current => keepNewerAssessment(current, out));
      queryClient.setQueryData<ReportOut>(["report", out.report.check_run_id], current => keepNewerAssessment(current, out.report));
      // BUG-033: the report's flags panel is its own query
      // (["report", id, "flags"], useReport.ts's useFlags) — without
      // this, an instructor who overrides here and clicks "View updated
      // report" would see a stale ACTIVE row in that panel until a
      // manual refetch, reintroducing the exact "overridden must not
      // read the same as active" failure this ticket exists to fix, one
      // layer up (design review spec, required not optional).
      queryClient.invalidateQueries({ queryKey: ["report", out.report.check_run_id, "flags"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}

// BUG-078: byte-identical onSuccess shape to useOverrideFlag (same three
// setQueryData/invalidateQueries calls) — reusing it means the report's
// flags panel correctly stops showing this flag as active, for free.
export function useConfirmCitationSource(flagId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    onMutate: () => flagMutationOwner(queryClient, flagId),
    mutationFn: async (body: { reason: string; expected_revision: number }) => {
      if (!isAssessmentRevision(body.expected_revision)) throw new ApiError(409, "conflict", "Review the current assessment before confirming a source.");
      const out = await api.post<ConfirmCitationSourceOut>(`/flags/${flagId}/confirm-source`, body);
      if (!out || typeof out !== "object" || out.id !== flagId || !out.overridden || !out.confirmed_citation_source || out.override_reason !== body.reason
        || out.assessment_revision !== body.expected_revision + 1 || out.report?.check_run_id !== out.check_run_id
        || out.report.assessment_revision !== out.assessment_revision) {
        throw new ApiError(502, "invalid_response", "The response did not confirm this source judgment. Review the current finding before trying again.");
      }
      return out;
    },
    onSuccess: (out, _input, owner) => {
      if (!owner?.accepts(out) || out.report?.check_run_id !== out.check_run_id) return;
      queryClient.setQueryData<FlagOut>(["flag", flagId], current => keepNewerAssessment(current, out));
      queryClient.setQueryData<ReportOut>(["report", out.report.check_run_id], current => keepNewerAssessment(current, out.report));
      queryClient.invalidateQueries({ queryKey: ["report", out.report.check_run_id, "flags"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}
