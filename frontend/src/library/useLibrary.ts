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

export function useLibraryItem(manuscriptId: number | undefined, enabled = true) {
  const validId = manuscriptId !== undefined && Number.isInteger(manuscriptId) && manuscriptId > 0;
  return useQuery({
    queryKey: ["library-item", manuscriptId],
    queryFn: () => api.get<LibraryItemOut>(`/library/${manuscriptId}`),
    enabled: enabled && validId,
  });
}

// The bounded, cross-tenant-safe view (Q2's ruling) -- always fetched for
// a NOT-owned manuscript; never for an owned one (the own-document query
// below is what renders instead).
export function useLibraryExcerpt(manuscriptId: number | undefined, enabled: boolean) {
  const validId = manuscriptId !== undefined && Number.isInteger(manuscriptId) && manuscriptId > 0;
  return useQuery({
    queryKey: ["library-excerpt", manuscriptId],
    queryFn: () => api.get<LibraryExcerptOut>(`/library/${manuscriptId}/excerpt`),
    enabled: enabled && validId,
  });
}

export function useLibraryDocument(manuscriptId: number | undefined, enabled: boolean) {
  const validId = manuscriptId !== undefined && Number.isInteger(manuscriptId) && manuscriptId > 0;
  return useQuery({
    queryKey: ["library-document", manuscriptId],
    queryFn: () => api.get<ManuscriptViewerOut>(`/library/${manuscriptId}/document`),
    enabled: enabled && validId,
  });
}

export function useLibraryParagraphs(manuscriptId: number | undefined, enabled: boolean) {
  const validId = manuscriptId !== undefined && Number.isInteger(manuscriptId) && manuscriptId > 0;
  return useQuery({
    queryKey: ["library-paragraphs", manuscriptId],
    queryFn: () => api.get<DocumentParagraphsOut>(`/library/${manuscriptId}/document/paragraphs`),
    enabled: enabled && validId,
  });
}

// Purge itself is unchanged from V-042 (still `/archive/{id}`, still
// ownership-gated server-side) -- Library just calls the same endpoint
// Archive used to.
//
// BUG-148: the in-place `setQueriesData` patch this used to do (still
// "Content removed" in place, no refetch flicker) only ever updated the
// TOP-LEVEL representative row, keyed on `manuscript_id`. Since a purge
// target can now be a SIBLING nested in `duplicate_uploads` (never a key
// in `old.items` itself), that patch would silently no-op for exactly the
// new interaction this ticket adds -- worse, purging the group's current
// representative can change WHICH manuscript the server now considers
// representative (an older still-stored sibling takes over), which a
// client-side patch has no way to re-derive without duplicating the
// backend's own tie-break rule. A real refetch is the only honest option
// once collapsing is server-side and non-trivial -- correctness over the
// no-flicker cosmetic.
export function usePurgeManuscript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manuscriptId: number) => api.delete<PurgeOut>(`/archive/${manuscriptId}`),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["library"] });
      queryClient.invalidateQueries({ queryKey: ["library-item", result.manuscript_id] });
    },
  });
}
