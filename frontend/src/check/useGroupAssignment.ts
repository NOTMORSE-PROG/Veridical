// F1, screens 4a/4e: one auth/form lifetime for proposal and saved-group actions.
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import {
  isKnownAssignmentRejection, UnconfirmedGroupAssignmentError,
  validateManuscriptGroup, validateProposalOutcome, validateSelectionOutcome,
  type GroupAssignmentOutput, type GroupAssignmentSubmission,
} from "../api/groupAssignmentOutcome";
import type { ManuscriptGroup } from "../api/types";
import { StaleUploadContextError } from "../api/uploadOutcome";
import { useUploadScope } from "../auth/useUploadScope";
import { ownedGroupQueryKey, useGroupQueryOwner } from "./groupQueryOwner";
import { clearGroupDraft, keepGroupDraft, normalizeName, readGroupDraft } from "./groupDraft";

export type AssignmentRecovery = "saved_details" | "current_assignment";
export type AssignmentState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "rejected"; message: string; recovery: AssignmentRecovery | null; error?: unknown }
  | { kind: "saved"; result: GroupAssignmentOutput }
  | { kind: "unknown"; checking: boolean; current?: ManuscriptGroup; readError?: string };

export function useGroupAssignment(manuscriptId: number) {
  const scope = useUploadScope(`group-assignment:${manuscriptId}`);
  const client = useQueryClient();
  const readOwner = useGroupQueryOwner();
  const [state, setState] = useState<AssignmentState>({ kind: "idle" });
  const busy = useRef(false);
  const unknown = useRef(false);
  const recovery = useRef<AssignmentRecovery | null>(null);

  function invalidateViews() {
    for (const key of ["manuscripts", "groups", "saved-group", "manuscript-group", "group-proposal",
      "group-collision", "report", "audit-log", "stored-files", "stats"]) {
      void client.invalidateQueries({ queryKey: [key] });
    }
  }

  async function submit(submission: GroupAssignmentSubmission) {
    if (busy.current || unknown.current || scope.status !== "ready" || !scope.isCurrent()) return;
    if (recovery.current === "current_assignment" || (recovery.current === "saved_details" && submission.kind === "saved")) return;
    const attempt = scope.capture();
    // Inputs remain a description of what was reviewed, even if callers mutate
    // their own draft objects while the session check is pending.
    const sent = structuredClone(submission);
    busy.current = true;
    setState({ kind: "saving" });
    let dispatched = false;
    try {
      await attempt.verify();
      dispatched = true;
      const value = sent.kind === "saved"
        ? await api.post<unknown>(`/manuscripts/${manuscriptId}/group-selection`, sent.body)
        : await api.patch<unknown>(`/manuscripts/${manuscriptId}/group`, sent.body);
      if (!attempt.isCurrent()) return;
      const result = sent.kind === "saved"
        ? validateSelectionOutcome(value, manuscriptId, attempt.actorId, sent.body, sent.reviewed)
        : validateProposalOutcome(value);
      await attempt.verify();
      if (!attempt.isCurrent()) return;
      setState({ kind: "saved", result });
      // Any confirmed assignment replaces what was typed before, including a
      // saved group chosen instead.
      clearGroupDraft(attempt.actorId, manuscriptId);
      invalidateViews();
    } catch (error) {
      if (!attempt.isCurrent() || error instanceof StaleUploadContextError) return;
      if (sent.kind === "proposal") {
        const { group_name, member_names, program_id } = sent.body;
        keepGroupDraft(attempt.actorId, manuscriptId, { group_name, member_names, program_id, refused: !dispatched || isKnownAssignmentRejection(error) });
      }
      if (error instanceof ApiError && error.status === 401 && error.code === "unauthenticated") {
        attempt.rejectActor();
        return;
      }
      if (dispatched) {
        try { await attempt.verify(); }
        catch (sessionError) {
          if (sessionError instanceof StaleUploadContextError || !attempt.isCurrent()) return;
          // The sign-in boundary hides the form until verification recovers.
          // Preserve the original request's certainty without resending it.
        }
      }
      if (!dispatched || isKnownAssignmentRejection(error)) {
        recovery.current = error instanceof ApiError && error.status === 409
          ? error.code === "saved_group_changed" ? "saved_details" : "current_assignment" : null;
        setState({ kind: "rejected", message: error instanceof Error ? error.message : "Could not save the assignment.", recovery: recovery.current, error });
      } else {
        unknown.current = true;
        setState({ kind: "unknown", checking: false });
        invalidateViews();
      }
    } finally {
      busy.current = false;
    }
  }

  async function checkSavedAssignment() {
    if (!unknown.current || busy.current || scope.status !== "ready" || !scope.isCurrent()) return;
    const attempt = scope.capture();
    busy.current = true;
    setState({ kind: "unknown", checking: true });
    try {
      await attempt.verify();
      const value = await api.get<unknown>(`/manuscripts/${manuscriptId}/group`);
      if (!attempt.isCurrent()) return;
      const current = validateManuscriptGroup(value, manuscriptId);
      await attempt.verify();
      if (!attempt.isCurrent()) return;
      client.setQueryData(ownedGroupQueryKey("manuscript-group", readOwner, manuscriptId), current);
      // The read shows the typed group was saved after all: nothing to restore.
      const draft = readGroupDraft(attempt.actorId, manuscriptId);
      if (draft && current.group && normalizeName(current.group.name) === normalizeName(draft.group_name)) clearGroupDraft(attempt.actorId, manuscriptId);
      setState({ kind: "unknown", checking: false, current });
    } catch (error) {
      if (!attempt.isCurrent() || error instanceof StaleUploadContextError) return;
      if (error instanceof ApiError && error.status === 401 && error.code === "unauthenticated") {
        attempt.rejectActor();
        return;
      }
      setState({ kind: "unknown", checking: false, readError: "Could not verify the saved assignment. Check your connection and try again." });
    } finally {
      busy.current = false;
    }
  }

  function continueAfterReadback(): ManuscriptGroup | undefined {
    if (state.kind !== "unknown" || !state.current || state.checking || busy.current || !scope.isCurrent()) return;
    const current = state.current;
    unknown.current = false;
    recovery.current = null;
    setState({ kind: "idle" });
    return current;
  }

  function markRecoveryReviewed(reviewed: AssignmentRecovery) {
    if (state.kind !== "rejected" || state.recovery !== reviewed || recovery.current !== reviewed
      || scope.status !== "ready" || !scope.isCurrent() || busy.current) return;
    recovery.current = null;
    setState({ kind: "idle" });
  }

  return {
    state, scope, submit, checkSavedAssignment, continueAfterReadback, markRecoveryReviewed,
    locked: state.kind === "saving" || state.kind === "unknown" || state.kind === "saved" || scope.status !== "ready",
    unknownMessage: new UnconfirmedGroupAssignmentError().message,
  };
}
