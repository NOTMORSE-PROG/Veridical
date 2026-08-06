// Check-run data layer (F3.1-F3.6/F8.1, screens 4f/4g): manuscript picker
// for the New Check modal, create-check mutation, and the progress
// screen's polling query.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CheckRun, PaginatedManuscripts } from "../api/types";

/** The New Check modal just wants "everything ingested" — requests one
 * generously large page rather than needing its own picker pagination
 * (V-021's dashboard table owns real pagination for 100+ manuscripts). */
export function useManuscripts() {
  return useQuery({
    queryKey: ["manuscripts", "picker"],
    queryFn: () => api.get<PaginatedManuscripts>("/manuscripts?page=1&page_size=200"),
    select: (data) => data.items,
  });
}

export function useCreateCheckRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { manuscript_id: number; rubric_id: number }) =>
      api.post<CheckRun>("/check-runs", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["check-runs"] });
    },
  });
}

const TERMINAL_STATUSES = new Set(["done", "failed"]);

export function useCheckRun(id: number) {
  return useQuery({
    queryKey: ["check-run", id],
    queryFn: () => api.get<CheckRun>(`/check-runs/${id}`),
    // Stops polling once the run is terminal — a finished run never
    // changes again, so there's nothing left to poll for (screen 4g).
    // Stopping on a persisting fetch error too is not just an
    // optimization: without it, `query.state.data` never becomes
    // defined, so this callback keeps returning 2_000 forever and the
    // query never settles into `isError` — confirmed live (ux-critic,
    // V-055 4g review) as an indefinite "Loading…" state that silently
    // re-polled a dead/404'd check run 75+ times over 65+ seconds,
    // `aria-busy="true"` the whole time. The "Try again" button still
    // triggers a fresh manual fetch regardless of this setting.
    refetchInterval: (query) => {
      if (query.state.status === "error") return false;
      const status = query.state.data?.status;
      return status && TERMINAL_STATUSES.has(status) ? false : 2_000;
    },
  });
}
