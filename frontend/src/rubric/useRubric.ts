// Rubric data layer (F2.1/F2.3/F2.4): upload, fetch, the criteria-review
// save/confirm mutation, and versioning (screens 4c/4d/4m).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import type { CitationRecencySettingsIn, CitationRecencySettingsOut, CriterionType, Rubric, RubricAcknowledgementReason, RubricListItem, RubricLevel } from "../api/types";
import { verifyUploadFailure, type UploadAttemptScope } from "../auth/useUploadScope";
import { StaleUploadContextError, UnconfirmedUploadError, validateRubricReceipt } from "../api/uploadOutcome";

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
      scope,
    }: {
      file: File;
      title: string;
      familyId?: string;
      scope: UploadAttemptScope;
    }) => {
      await scope.verify();
      const form = new FormData();
      form.append("file", file);
      const query = new URLSearchParams({ title, ...(familyId ? { family_id: familyId } : {}) });
      let receipt;
      try {
        const res = await fetch(`${BASE_URL}/rubrics?${query.toString()}`, {
          method: "POST",
          credentials: "include",
          headers: { "X-Veridical-Expected-Instructor": String(scope.actorId) },
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
        const out: unknown = await res.json();
        if (!scope.isCurrent()) throw new StaleUploadContextError();
        receipt = validateRubricReceipt(out, familyId);
      } catch (error) {
        return verifyUploadFailure(error, scope);
      }
      try { await scope.verify(); }
      catch (error) {
        if (error instanceof StaleUploadContextError) throw error;
        throw new UnconfirmedUploadError();
      }
      return receipt;
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

export interface SaveCriteriaBody {
  criteria: CriterionEdit[];
  confirm: boolean;
  // BUG-566: the revision these edits were made on, so a save never
  // overwrites a change made in another tab, and the parser issues the
  // instructor chose to keep as written.
  expected_revision?: number | null;
  acknowledgements?: RubricAcknowledgement[];
}

export interface RubricAcknowledgement {
  issue_id: number;
  reason_code: RubricAcknowledgementReason;
  note: string | null;
}

export function useSaveCriteria(rubricId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SaveCriteriaBody) =>
      api.put<Rubric>(`/rubrics/${rubricId}/criteria`, body),
    onSuccess: (rubric) => {
      queryClient.setQueryData(rubricQueryKey(rubricId), rubric);
    },
  });
}

export function useRubricFamilies({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["rubric-families"],
    queryFn: () => api.get<RubricListItem[]>("/rubric-families"),
    enabled,
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

// V-092: the citation recency note's setting for the WHOLE family. An empty
// body clears it back to the configured defaults. Existing reports keep the
// values their check recorded.
export function useSetCitationRecency() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ familyId, body }: { familyId: string; body: CitationRecencySettingsIn }) =>
      api.put<CitationRecencySettingsOut>(`/rubric-families/${familyId}/citation-recency`, body),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["rubric"] }); },
  });
}
