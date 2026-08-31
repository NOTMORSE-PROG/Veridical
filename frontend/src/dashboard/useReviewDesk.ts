import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PaginatedManuscripts } from "../api/types";

export type ReviewDeskQueue =
  | "needs_review"
  | "checking"
  | "ready_to_decide"
  | "complete"
  | "not_checked";

export type ReviewDeskSort = "newest" | "oldest" | "group_asc" | "needs_review_desc";

export interface ReviewDeskQuery {
  queue: ReviewDeskQueue;
  q?: string;
  program?: string;
  sort: ReviewDeskSort;
  page: number;
  pageSize: number;
}

const QUEUE_FILTERS: Record<ReviewDeskQueue, Readonly<Record<string, string>>> = {
  needs_review: { status: "needs_attention" },
  checking: { status: "checking" },
  ready_to_decide: { status: "checked", needs_review: "false" },
  complete: { status: "decided" },
  not_checked: { status: "not_checked" },
};

export function reviewDeskPath(query: ReviewDeskQuery): string {
  const params = new URLSearchParams({
    queue: query.queue,
    sort: query.sort,
    page: String(query.page),
    page_size: String(query.pageSize),
    ...QUEUE_FILTERS[query.queue],
  });
  if (query.q) params.set("q", query.q);
  if (query.program) params.set("program", query.program);
  return `/manuscripts?${params.toString()}`;
}

export function useReviewDeskPage(query: ReviewDeskQuery) {
  return useQuery({
    queryKey: ["manuscripts", "review-desk", query],
    queryFn: () => api.get<PaginatedManuscripts>(reviewDeskPath(query)),
  });
}

export function useDismissFailedManuscript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manuscriptId: number) =>
      api.post<{ manuscript_id: number; dismissed_at: string }>(`/manuscripts/${manuscriptId}/dismiss`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["manuscripts"] }),
  });
}
