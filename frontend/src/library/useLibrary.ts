// V-077 (D-028): the Library as a standalone collection and the only
// originality corpus. Replaces the V-066 hooks (purge, duplicate-upload
// groups, report links), which belonged to the old "every upload is a
// Library record" model.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  DocumentParagraphsOut,
  LibraryEntryStateOut,
  LibraryEntryUpdate,
  LibraryExcerptOut,
  LibraryItemOut,
  ManuscriptViewerOut,
  PaginatedLibrary,
} from "../api/types";
import { LIBRARY_PAGE_SIZE, LIBRARY_PENDING_PAGE_SIZE, LIBRARY_STATUS_POLL_MS } from "../config/ui";

function isValidId(manuscriptId: number | undefined): manuscriptId is number {
  return manuscriptId !== undefined && Number.isInteger(manuscriptId) && manuscriptId > 0;
}

/** The collection itself: active records only, own and shared. */
export function useLibrary(page: number, program: string | undefined, search: string) {
  const params = new URLSearchParams({ page: String(page), page_size: String(LIBRARY_PAGE_SIZE) });
  if (program) params.set("program", program);
  if (search.trim()) params.set("search", search.trim());
  return useQuery({
    queryKey: ["library", "active", page, program, search],
    queryFn: () => api.get<PaginatedLibrary>(`/library?${params.toString()}`),
  });
}

/** The requester's own uploads that are not comparison sources yet
 * (still reading, waiting for a duplicate decision, or unreadable). Kept
 * apart from the collection so its count never includes them. */
export function useLibraryPending() {
  const params = new URLSearchParams({
    scope: "pending",
    page: "1",
    page_size: String(LIBRARY_PENDING_PAGE_SIZE),
  });
  return useQuery({
    queryKey: ["library", "pending"],
    queryFn: () => api.get<PaginatedLibrary>(`/library?${params.toString()}`),
  });
}

export function useLibraryItem(manuscriptId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: ["library-item", manuscriptId],
    queryFn: () => api.get<LibraryItemOut>(`/library/${manuscriptId}`),
    enabled: enabled && isValidId(manuscriptId),
    // A record still being read refreshes until it settles.
    refetchInterval: (query) =>
      query.state.data?.status === "processing" ? LIBRARY_STATUS_POLL_MS : false,
  });
}

// Bounded excerpt API retained for existing evidence-reading callers.
// V-079 uses the full document endpoint for active shared records.
export function useLibraryExcerpt(manuscriptId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["library-excerpt", manuscriptId],
    queryFn: () => api.get<LibraryExcerptOut>(`/library/${manuscriptId}/excerpt`),
    enabled: enabled && isValidId(manuscriptId),
  });
}

export function useLibraryDocument(manuscriptId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["library-document", manuscriptId],
    queryFn: () => api.get<ManuscriptViewerOut>(`/library/${manuscriptId}/document`),
    enabled: enabled && isValidId(manuscriptId),
  });
}

export function useLibraryParagraphs(manuscriptId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["library-paragraphs", manuscriptId],
    queryFn: () => api.get<DocumentParagraphsOut>(`/library/${manuscriptId}/document/paragraphs`),
    enabled: enabled && isValidId(manuscriptId),
  });
}

export interface LibrarySubmission {
  file: File;
  program?: string;
  schoolYear?: string;
  title?: string;
  teamName?: string;
  authors?: string[];
}

function useInvalidateLibrary() {
  const queryClient = useQueryClient();
  return (manuscriptId?: number, removed = false) => {
    void queryClient.invalidateQueries({ queryKey: ["library"] });
    if (manuscriptId === undefined) return;
    if (removed) {
      // A removed record no longer exists for anyone; refetching it only
      // produces a 404 (UX review, 2026-10-13).
      queryClient.removeQueries({ queryKey: ["library-item", manuscriptId] });
      queryClient.removeQueries({ queryKey: ["library-document", manuscriptId] });
    } else {
      void queryClient.invalidateQueries({ queryKey: ["library-item", manuscriptId] });
    }
  };
}

export function useLibraryInvalidation() {
  return useInvalidateLibrary();
}

export function useUpdateLibraryEntry(manuscriptId: number) {
  const invalidate = useInvalidateLibrary();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (update: LibraryEntryUpdate) =>
      api.patch<LibraryItemOut>(`/library/${manuscriptId}`, update),
    onSuccess: (item) => {
      queryClient.setQueryData(["library-item", manuscriptId], item);
      invalidate();
    },
  });
}

export function useDecideLibraryEntry() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: ({ manuscriptId, action }: { manuscriptId: number; action: "add" | "skip" }) =>
      api.post<LibraryEntryStateOut>(`/library/${manuscriptId}/decision`, { action }),
    onSuccess: (state) => invalidate(state.manuscript_id, state.status === "removed"),
  });
}

export function useRemoveLibraryEntry() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (manuscriptId: number) =>
      api.post<LibraryEntryStateOut>(`/library/${manuscriptId}/remove`),
    onSuccess: (state) => invalidate(state.manuscript_id, true),
  });
}
