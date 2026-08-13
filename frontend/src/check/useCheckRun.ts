// Check-run data layer (F3.1-F3.6/F8.1, screens 4f/4g): manuscript picker
// for the New Check modal, create-check mutation, and the progress
// screen's polling query.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CheckRun, IngestSummary, PaginatedManuscripts } from "../api/types";

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

// V-059: the upload screen the product's own description promises
// ("uploads a required format... and a manuscript") but never shipped.
// `group_label` is a query parameter on the real endpoint, not a form
// field (confirmed live against the running OpenAPI schema) -- same
// query-param-plus-FormData shape `useUploadRubric` already uses for
// `title`/`familyId`.
export function useIngestManuscript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, groupLabel }: { file: File; groupLabel: string }) => {
      const form = new FormData();
      form.append("file", file);
      const trimmed = groupLabel.trim();
      const query = trimmed ? `?group_label=${encodeURIComponent(trimmed)}` : "";
      return api.post<IngestSummary>(`/manuscripts/ingest${query}`, form);
    },
    onSuccess: () => {
      // Invalidates BOTH ["manuscripts", "picker"] (New Check's dropdown)
      // and ["manuscripts", "table", ...] (the dashboard table) -- TanStack
      // Query matches by key prefix, so one call covers both without
      // either query needing to know about the other.
      queryClient.invalidateQueries({ queryKey: ["manuscripts"] });
    },
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
