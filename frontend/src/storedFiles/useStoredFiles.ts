// V-078 (BUG-410): the stored files page's data. `GET /archive` lists the
// requester's own Review Desk uploads; `DELETE /archive/{id}` is the V-042
// purge (deletes the stored upload and extracted text, keeps reports,
// decisions and audit history).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PaginatedStoredFiles, PurgeOut } from "../api/types";
import { STORED_FILES_PAGE_SIZE } from "../config/ui";

export function useStoredFiles(page: number) {
  const params = new URLSearchParams({ page: String(page), page_size: String(STORED_FILES_PAGE_SIZE) });
  return useQuery({
    queryKey: ["stored-files", page],
    queryFn: () => api.get<PaginatedStoredFiles>(`/archive?${params.toString()}`),
  });
}

export function useDeleteStoredFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manuscriptId: number) => api.delete<PurgeOut>(`/archive/${manuscriptId}`),
    onSuccess: (result) => {
      // Show the deleted state at once so focus can land on the row before
      // the refetch settles; the refetch then confirms it.
      queryClient.setQueriesData<PaginatedStoredFiles>({ queryKey: ["stored-files"] }, (current) =>
        current && {
          ...current,
          items: current.items.map((item) =>
            item.manuscript_id === result.manuscript_id
              ? { ...item, purged_at: result.purged_at, has_stored_file: false, has_archive: false, in_library: false }
              : item,
          ),
        },
      );
      void queryClient.invalidateQueries({ queryKey: ["stored-files"] });
      // A legacy Library record built from this upload leaves the Library.
      void queryClient.invalidateQueries({ queryKey: ["library"] });
      void queryClient.invalidateQueries({ queryKey: ["library-item", result.manuscript_id] });
      void queryClient.invalidateQueries({ queryKey: ["manuscripts"] });
    },
  });
}
