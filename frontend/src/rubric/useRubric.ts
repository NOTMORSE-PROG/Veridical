// Rubric data layer (F2.1/F2.3/F2.4): upload, fetch, the criteria-review
// save/confirm mutation, and versioning (screens 4c/4d/4m).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { CriterionType, Rubric, RubricListItem, RubricLevel } from "../api/types";

export function rubricQueryKey(id: number) {
  return ["rubric", id] as const;
}

export function useRubric(id: number) {
  return useQuery({
    queryKey: rubricQueryKey(id),
    queryFn: () => api.get<Rubric>(`/rubrics/${id}`),
  });
}

// BUG-003: same default as api/client.ts — kept in sync deliberately,
// since this file's multipart FormData upload can't go through the shared
// `request()` wrapper (it must not set a JSON Content-Type).
const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

export function useUploadRubric() {
  return useMutation({
    mutationFn: async ({
      file,
      title,
      familyId,
    }: {
      file: File;
      title: string;
      familyId?: string;
    }) => {
      const form = new FormData();
      form.append("file", file);
      const query = new URLSearchParams({ title, ...(familyId ? { family_id: familyId } : {}) });
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
  // V-069: round-tripped as-is, never hand-edited on this screen in this
  // ticket -- omitting this field on save would silently strip a
  // decomposed scale back down to prose, since Save/Confirm REPLACES the
  // full criteria set every time.
  levels?: RubricLevel[] | null;
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

export function useRubricFamilies() {
  return useQuery({
    queryKey: ["rubric-families"],
    queryFn: () => api.get<RubricListItem[]>("/rubric-families"),
  });
}

export function rubricVersionsQueryKey(familyId: string) {
  return ["rubric-versions", familyId] as const;
}

export function useRubricVersions(familyId: string | undefined) {
  return useQuery({
    queryKey: rubricVersionsQueryKey(familyId ?? ""),
    queryFn: () => api.get<RubricListItem[]>(`/rubric-families/${familyId}/versions`),
    enabled: familyId !== undefined,
  });
}

function useInvalidateVersionLists() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["rubric-families"] });
    queryClient.invalidateQueries({ queryKey: ["rubric-versions"] });
  };
}

export function useActivateRubric() {
  const invalidate = useInvalidateVersionLists();
  return useMutation({
    mutationFn: (rubricId: number) => api.post<Rubric>(`/rubrics/${rubricId}/activate`),
    onSuccess: invalidate,
  });
}

export function useDeleteRubric() {
  const invalidate = useInvalidateVersionLists();
  return useMutation({
    mutationFn: (rubricId: number) => api.delete(`/rubrics/${rubricId}`),
    onSuccess: invalidate,
  });
}

// V-064 (AC1, screen 4m): sets (or clears, program_id: null) the WHOLE
// family's program in one write -- every version, not just the one
// currently shown.
export function useSetRubricFamilyProgram() {
  const invalidate = useInvalidateVersionLists();
  return useMutation({
    mutationFn: ({ familyId, programId }: { familyId: string; programId: number | null }) =>
      api.put<RubricListItem[]>(`/rubric-families/${familyId}/program`, { program_id: programId }),
    onSuccess: invalidate,
  });
}
