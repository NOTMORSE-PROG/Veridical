// Flag evidence/annotation/override data layer (F8.2/F8.4, screen 4i).
import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ConfirmCitationSourceOut, FlagOut, Instructor, OverrideFlagOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";

function captureActionActor(client: QueryClient, expectedRun?: number) {
  if (expectedRun === undefined) return () => {};
  const query = client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true });
  const actor = client.getQueryData<Instructor | null>(ME_QUERY_KEY)?.id;
  return () => {
    if (!actor || !query) throw new Error("Sign in again before saving this action.");
    if (client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) !== query
      || query.state.status !== "success" || client.getQueryData<Instructor | null>(ME_QUERY_KEY)?.id !== actor) {
      throw new Error("The instructor session changed. Reopen this finding before saving.");
    }
  };
}

function requireActionReply(out: FlagOut, flagId: number, expectedRun?: number) {
  if (expectedRun !== undefined && (out.id !== flagId || out.check_run_id !== expectedRun)) {
    throw new Error("The saved reply did not match this finding. Reopen it to verify the record.");
  }
}

export function useFlag(flagId: number) {
  return useQuery({
    queryKey: ["flag", flagId],
    queryFn: () => api.get<FlagOut>(`/flags/${flagId}`),
    enabled: Number.isInteger(flagId) && flagId > 0,
  });
}

export function useAnnotateFlag(flagId: number, expectedRun?: number) {
  const queryClient = useQueryClient();
  const checkActor = captureActionActor(queryClient, expectedRun);
  return useMutation({
    mutationFn: async (annotation: string) => {
      checkActor();
      const out = await api.post<FlagOut>(`/flags/${flagId}/annotate`, { annotation });
      checkActor();
      requireActionReply(out, flagId, expectedRun);
      if (expectedRun !== undefined && out.annotation !== annotation) throw new Error("The saved reply did not match your note. Reopen this finding to check it.");
      return out;
    },
    onSuccess: (out) => { checkActor(); queryClient.setQueryData(["flag", flagId], out); },
  });
}

export function useOverrideFlag(flagId: number, expectedRun?: number) {
  const queryClient = useQueryClient();
  const checkActor = captureActionActor(queryClient, expectedRun);
  return useMutation({
    mutationFn: async (reason: string) => {
      checkActor();
      const out = await api.post<OverrideFlagOut>(`/flags/${flagId}/override`, { reason });
      checkActor();
      requireActionReply(out, flagId, expectedRun);
      if (expectedRun !== undefined && (!out.overridden || out.override_reason !== reason || out.report.check_run_id !== expectedRun)) throw new Error("The saved reply did not confirm that this finding was dismissed. Reopen it to check.");
      return out;
    },
    onSuccess: (out) => {
      checkActor();
      queryClient.setQueryData(["flag", flagId], out);
      queryClient.setQueryData(["report", out.report.check_run_id], out.report);
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
export function useConfirmCitationSource(flagId: number, expectedRun?: number) {
  const queryClient = useQueryClient();
  const checkActor = captureActionActor(queryClient, expectedRun);
  return useMutation({
    mutationFn: async (reason: string) => {
      checkActor();
      const out = await api.post<ConfirmCitationSourceOut>(`/flags/${flagId}/confirm-source`, { reason });
      checkActor();
      requireActionReply(out, flagId, expectedRun);
      if (expectedRun !== undefined && (!out.confirmed_citation_source || !out.overridden || out.override_reason !== reason || out.report.check_run_id !== expectedRun)) throw new Error("The citation reply did not confirm this action.");
      return out;
    },
    onSuccess: (out) => {
      checkActor();
      queryClient.setQueryData(["flag", flagId], out);
      queryClient.setQueryData(["report", out.report.check_run_id], out.report);
      queryClient.invalidateQueries({ queryKey: ["report", out.report.check_run_id, "flags"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });
}
