// Rubric data layer (F2.1/F2.3): upload, fetch, and the criteria-review
// save/confirm mutation (screens 4c/4d).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { CriterionType, Rubric } from "../api/types";

export function rubricQueryKey(id: number) {
  return ["rubric", id] as const;
}

export function useRubric(id: number) {
  return useQuery({
    queryKey: rubricQueryKey(id),
    queryFn: () => api.get<Rubric>(`/rubrics/${id}`),
  });
}

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

export function useUploadRubric() {
  return useMutation({
    mutationFn: async ({ file, title }: { file: File; title: string }) => {
      const form = new FormData();
      form.append("file", file);
      const query = new URLSearchParams({ title });
      const res = await fetch(`${BASE_URL}/rubrics?${query.toString()}`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as
          | { error?: { code?: string; message?: string } }
          | null;
        throw new ApiError(
          res.status,
          body?.error?.code ?? "internal",
          body?.error?.message ?? `Upload failed (${res.status}).`,
        );
      }
      return (await res.json()) as Rubric;
    },
  });
}

export interface CriterionEdit {
  id: number | null;
  type: CriterionType;
  text: string;
  evidence: string | null;
  weight: number;
}

export function useSaveCriteria(rubricId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { criteria: CriterionEdit[]; confirm: boolean }) =>
      api.put<Rubric>(`/rubrics/${rubricId}/criteria`, body),
    onSuccess: (rubric) => {
      queryClient.setQueryData(rubricQueryKey(rubricId), rubric);
    },
  });
}
