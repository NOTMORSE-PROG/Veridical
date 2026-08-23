// V-066 (screen 4w) — the shared-corpus library. Replaces the old
// useArchive.ts (Archive is retired, Library is a strict superset).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  DocumentParagraphsOut,
  LibraryExcerptOut,
  LibraryItemOut,
  ManuscriptViewerOut,
  PaginatedLibrary,
  PurgeOut,
} from "../api/types";

export function useLibrary(page: number, program: string | undefined, search: string) {
  const params = new URLSearchParams({ page: String(page), page_size: "50" });
  if (program) params.set("program", program);
  if (search.trim()) params.set("search", search.trim());
  return useQuery({
    queryKey: ["library", page, program, search],
    queryFn: () => api.get<PaginatedLibrary>(`/library?${params.toString()}`),
  });
}

export function useLibraryItem(manuscriptId: number | undefined) {
  return useQuery({
    queryKey: ["library-item", manuscriptId],
    queryFn: () => api.get<LibraryItemOut>(`/library/${manuscriptId}`),
    enabled: manuscriptId !== undefined,
  });
}

// The bounded, cross-tenant-safe view (Q2's ruling) -- always fetched for
// a NOT-owned manuscript; never for an owned one (the own-document query
// below is what renders instead).
export function useLibraryExcerpt(manuscriptId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["library-excerpt", manuscriptId],
    queryFn: () => api.get<LibraryExcerptOut>(`/library/${manuscriptId}/excerpt`),
    enabled: enabled && manuscriptId !== undefined,
  });
}

export function useLibraryDocument(manuscriptId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["library-document", manuscriptId],
    queryFn: () => api.get<ManuscriptViewerOut>(`/library/${manuscriptId}/document`),
    enabled: enabled && manuscriptId !== undefined,
  });
}

export function useLibraryParagraphs(manuscriptId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["library-paragraphs", manuscriptId],
    queryFn: () => api.get<DocumentParagraphsOut>(`/library/${manuscriptId}/document/paragraphs`),
    enabled: enabled && manuscriptId !== undefined,
  });
}

// Purge itself is unchanged from V-042 (still `/archive/{id}`, still
// ownership-gated server-side) -- Library just calls the same endpoint
// Archive used to, and patches the SAME `library` query cache Archive used
// to patch on its own `archive` cache, so a purge shows as "Content
// removed" in place, not a refetch flicker.
export function usePurgeManuscript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manuscriptId: number) => api.delete<PurgeOut>(`/archive/${manuscriptId}`),
    onSuccess: (result) => {
      queryClient.setQueriesData<PaginatedLibrary>({ queryKey: ["library"] }, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.manuscript_id === result.manuscript_id
                  ? { ...item, purged_at: result.purged_at }
                  : item,
              ),
            }
          : old,
      );
      queryClient.invalidateQueries({ queryKey: ["library-item", result.manuscript_id] });
    },
  });
}
