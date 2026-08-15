// V-042 (screen 4t) — the F7 originality/reuse archive.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PaginatedArchive, PurgeOut } from "../api/types";

export function useArchive(page = 1, pageSize = 50) {
  return useQuery({
    queryKey: ["archive", page, pageSize],
    queryFn: () => api.get<PaginatedArchive>(`/archive?page=${page}&page_size=${pageSize}`),
  });
}

export function usePurgeManuscript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (manuscriptId: number) => api.delete<PurgeOut>(`/archive/${manuscriptId}`),
    onSuccess: (result) => {
      // Patch every cached page in place rather than invalidate+refetch --
      // the AC is that the row visibly becomes "content removed", not that
      // it disappears or flickers through a loading state.
      queryClient.setQueriesData<PaginatedArchive>({ queryKey: ["archive"] }, (old) =>
        old
          ? {
              ...old,
              items: old.items.map((item) =>
                item.manuscript_id === result.manuscript_id
                  ? { ...item, has_archive: false, purged_at: result.purged_at }
                  : item,
              ),
            }
          : old,
      );
    },
  });
}
