// BUG-560: group details the instructor typed survive a save that failed or
// could not be confirmed, so closing the form does not mean retyping a whole
// group. Kept for this tab only, per instructor and manuscript.
export interface GroupDraft {
  group_name: string;
  member_names: string[];
  program_id: number | null;
}

const key = (actorId: number, manuscriptId: number) => `veridical.group-draft.v1.${actorId}.${manuscriptId}`;

export function readGroupDraft(actorId: number | undefined, manuscriptId: number): GroupDraft | null {
  if (actorId === undefined) return null;
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(key(actorId, manuscriptId)) ?? "null");
    if (typeof value !== "object" || value === null) return null;
    const draft = value as Partial<GroupDraft>;
    if (typeof draft.group_name !== "string" || !Array.isArray(draft.member_names)
      || !draft.member_names.every((name) => typeof name === "string")
      || !(draft.program_id === null || Number.isSafeInteger(draft.program_id))) return null;
    return { group_name: draft.group_name, member_names: draft.member_names, program_id: draft.program_id ?? null };
  } catch {
    return null;
  }
}

export function keepGroupDraft(actorId: number, manuscriptId: number, draft: GroupDraft) {
  try { window.sessionStorage.setItem(key(actorId, manuscriptId), JSON.stringify(draft)); } catch { /* Not kept on this device. */ }
}

export function clearGroupDraft(actorId: number | undefined, manuscriptId: number) {
  if (actorId === undefined) return;
  try { window.sessionStorage.removeItem(key(actorId, manuscriptId)); } catch { /* Nothing to clear. */ }
}
