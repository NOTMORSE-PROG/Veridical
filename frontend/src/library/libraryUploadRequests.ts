// F7/F9, screen 4aa: each private write belongs to its captured upload context.
import { api } from "../api/client";
import type { LibraryEntryStateOut, LibrarySubmissionOut } from "../api/types";
import { StaleUploadContextError, UnconfirmedUploadError } from "../api/uploadOutcome";
import { verifyUploadFailure, type UploadAttemptScope } from "../auth/useUploadScope";
import type { LibrarySubmission } from "./useLibrary";

async function postOwned<T>(path: string, body: unknown, scope: UploadAttemptScope,
  onAdmitted?: () => void, validate?: (value: T) => void): Promise<T> {
  // Preflight failure means this operation has not sent a write.
  await scope.verify();
  if (!scope.isCurrent()) throw new StaleUploadContextError();
  onAdmitted?.();
  if (!scope.isCurrent()) throw new StaleUploadContextError();
  let out: T;
  try {
    out = await api.post<T>(path, body, { "X-Veridical-Expected-Instructor": String(scope.actorId) });
    if (!scope.isCurrent()) throw new StaleUploadContextError();
    validate?.(out);
  } catch (error) {
    return verifyUploadFailure(error, scope);
  }
  try { await scope.verify(); }
  catch (error) {
    if (error instanceof StaleUploadContextError) throw error;
    throw new UnconfirmedUploadError();
  }
  if (!scope.isCurrent()) throw new StaleUploadContextError();
  return out;
}

export function submitOwnedLibraryEntry(submission: LibrarySubmission, scope: UploadAttemptScope,
  onAdmitted?: () => void): Promise<LibrarySubmissionOut> {
  const form = new FormData();
  form.append("file", submission.file);
  if (submission.program) form.append("program", submission.program);
  if (submission.schoolYear) form.append("school_year", submission.schoolYear);
  if (submission.title) form.append("title", submission.title);
  if (submission.teamName) form.append("team_name", submission.teamName);
  for (const author of submission.authors ?? []) if (author.trim()) form.append("authors", author.trim());
  return postOwned("/library/entries", form, scope, onAdmitted);
}

function validateAction(value: LibraryEntryStateOut, manuscriptId: number, status: "active" | "removed") {
  if (!value || typeof value !== "object" || value.manuscript_id !== manuscriptId || value.status !== status) {
    throw new UnconfirmedUploadError();
  }
}

export function decideOwnedLibraryEntry(manuscriptId: number, action: "add" | "skip",
  scope: UploadAttemptScope, onAdmitted?: () => void): Promise<LibraryEntryStateOut> {
  return postOwned(`/library/${manuscriptId}/decision`, { action }, scope, onAdmitted,
    value => validateAction(value, manuscriptId, action === "add" ? "active" : "removed"));
}

export function removeOwnedLibraryEntry(manuscriptId: number, scope: UploadAttemptScope,
  onAdmitted?: () => void): Promise<LibraryEntryStateOut> {
  return postOwned(`/library/${manuscriptId}/remove`, undefined, scope, onAdmitted,
    value => validateAction(value, manuscriptId, "removed"));
}
