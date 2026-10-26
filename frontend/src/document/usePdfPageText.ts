import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";

export interface PdfPageText {
  check_run_id: number;
  manuscript_id: number;
  page: number;
  page_count: number | null;
  state: "available" | "no_selectable_text" | "unavailable";
  reason: "source_cache_unavailable" | "saved_text_unavailable" | "reading_order_not_recorded"
    | "reading_order_unresolved" | "source_binding_unverified" | "resource_limit" | "page_out_of_range" | null;
  blocks: { text: string; is_furniture: boolean }[];
  saved_block_count: number | null;
  omitted_block_count: number | null;
}

export function positivePage(value: string | null): number | null {
  if (value === null || !/^\d+$/.test(value.trim())) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

export function clearPdfReading(params: URLSearchParams) {
  for (const key of ["source", "text_page", "page"]) params.delete(key);
}

/** Reading mode and browsing page do not change the selected criterion task. */
export function sameSourceTask(left: string, right: string) {
  function normalized(value: string) {
    const params = new URLSearchParams(value);
    clearPdfReading(params);
    if (["manuscript", "review", "analysis"].includes(params.get("pane") ?? "")) params.delete("pane");
    params.sort();
    return params.toString();
  }
  return normalized(left) === normalized(right);
}

export function usePdfPageText(run: number, manuscript: number, actor: number | undefined, page: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ["private-page-text", actor, run, manuscript, page],
    queryFn: async ({ signal }) => {
      const result = await api.get<PdfPageText>(`/check-runs/${run}/document/page-text?page=${page}`, { signal });
      if (result.check_run_id !== run || result.manuscript_id !== manuscript || result.page !== page
        || !["available", "no_selectable_text", "unavailable"].includes(result.state)
        || !Array.isArray(result.blocks) || result.blocks.some((block) => typeof block.text !== "string")) {
        throw new ApiError(502, "invalid_response", "The saved text response did not match this source page.");
      }
      return result;
    },
    enabled: enabled && actor !== undefined && page !== null,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}
