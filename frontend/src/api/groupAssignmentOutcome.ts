// F1, screens 4a/4e: saved identity and proposal matching have distinct receipts.
import { ApiError } from "./client";
import type { ConfirmGroupResponse, CurrentGroup, ManuscriptGroup } from "./types";

export interface SavedGroup extends CurrentGroup { fingerprint: string }
export interface GroupSelectionInput {
  saved_group_id: number;
  fingerprint: string;
  expected_group_id: number | null;
}
export interface GroupSelectionReceipt {
  version: 2;
  manuscript_id: number;
  instructor_id: number;
  assigned_group_id: number;
  request: GroupSelectionInput;
  effective: SavedGroup;
}
export interface GroupSelectionOutput {
  action: "select_saved_group";
  manuscript_id: number;
  outcome: "selected" | "unchanged";
  receipt: GroupSelectionReceipt;
}
export interface GroupProposalInput {
  group_name: string;
  member_names: string[];
  program_id: number | null;
  title: string | null;
  expected_group_id: number | null;
}
export type GroupAssignmentSubmission =
  | { kind: "saved"; body: GroupSelectionInput; reviewed: SavedGroup }
  | { kind: "proposal"; body: GroupProposalInput };
export type GroupAssignmentOutput = GroupSelectionOutput | ConfirmGroupResponse;

export class UnconfirmedGroupAssignmentError extends Error {
  constructor() { super("The assignment result could not be confirmed. Check the saved assignment before trying again."); }
}
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0;
const nullableId = (value: unknown): value is number | null => value === null || positive(value);
const nullableString = (value: unknown): value is string | null => value === null || typeof value === "string";
const keys = (value: Record<string, unknown>, allowed: string[]) => Object.keys(value).length === allowed.length && allowed.every((key) => Object.hasOwn(value, key));
const fingerprint = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const groupKeys = ["id", "name", "member_names", "program_id", "program", "title"];

function isGroup(value: unknown): value is CurrentGroup {
  return object(value) && positive(value.id) && typeof value.name === "string" && value.name.trim() !== ""
    && Array.isArray(value.member_names) && value.member_names.every((name) => typeof name === "string")
    && nullableId(value.program_id) && nullableString(value.program) && nullableString(value.title);
}
function isSavedGroup(value: unknown): value is SavedGroup {
  return isGroup(value) && object(value) && keys(value, [...groupKeys, "fingerprint"]) && fingerprint(value.fingerprint);
}
export function sameSavedGroup(left: SavedGroup, right: SavedGroup) {
  return left.id === right.id && left.name === right.name && left.program_id === right.program_id
    && left.program === right.program && left.title === right.title && left.fingerprint === right.fingerprint
    && left.member_names.length === right.member_names.length && left.member_names.every((name, index) => name === right.member_names[index]);
}
export function validateSavedGroup(value: unknown, expectedId: number): SavedGroup {
  if (!isSavedGroup(value) || value.id !== expectedId) throw new Error("The saved group details could not be verified. Try loading them again.");
  return value;
}
export function validateManuscriptGroup(value: unknown, manuscriptId: number): ManuscriptGroup {
  if (!object(value) || value.manuscript_id !== manuscriptId
    || !(value.confirmation === "recorded" || value.confirmation === "not_recorded")
    || !(value.group === null || (isGroup(value.group) && keys(value.group as unknown as Record<string, unknown>, groupKeys)))) {
    throw new Error("The saved assignment could not be verified. Try loading it again.");
  }
  return value as unknown as ManuscriptGroup;
}

export function validateSelectionOutcome(
  value: unknown, manuscriptId: number, instructorId: number,
  submitted: GroupSelectionInput, reviewed: SavedGroup,
): GroupSelectionOutput {
  if (!object(value) || !keys(value, ["action", "manuscript_id", "outcome", "receipt"])
    || value.action !== "select_saved_group" || value.manuscript_id !== manuscriptId
    || !(value.outcome === "selected" || value.outcome === "unchanged") || !object(value.receipt)) throw new UnconfirmedGroupAssignmentError();
  const receipt = value.receipt;
  if (!keys(receipt, ["version", "manuscript_id", "instructor_id", "assigned_group_id", "request", "effective"])
    || receipt.version !== 2 || receipt.manuscript_id !== manuscriptId || receipt.instructor_id !== instructorId
    || receipt.assigned_group_id !== submitted.saved_group_id || !object(receipt.request)
    || !keys(receipt.request, ["saved_group_id", "fingerprint", "expected_group_id"])
    || !positive(receipt.request.saved_group_id) || !nullableId(receipt.request.expected_group_id)
    || !fingerprint(receipt.request.fingerprint) || receipt.request.saved_group_id !== submitted.saved_group_id
    || receipt.request.expected_group_id !== submitted.expected_group_id || receipt.request.fingerprint !== submitted.fingerprint
    || !isSavedGroup(receipt.effective) || !sameSavedGroup(receipt.effective, reviewed)
    || reviewed.id !== submitted.saved_group_id || reviewed.fingerprint !== submitted.fingerprint) throw new UnconfirmedGroupAssignmentError();
  return value as unknown as GroupSelectionOutput;
}

export function validateProposalOutcome(value: unknown): ConfirmGroupResponse {
  if (!object(value) || !positive(value.group_id) || typeof value.group_label !== "string" || value.group_label.trim() === ""
    || !nullableString(value.program) || !nullableString(value.title)
    || !(value.outcome === "created" || value.outcome === "matched" || value.outcome === "unchanged")
    || value.matched !== (value.outcome === "matched")) throw new UnconfirmedGroupAssignmentError();
  return value as unknown as ConfirmGroupResponse;
}

export function isKnownAssignmentRejection(error: unknown) {
  return error instanceof ApiError && (
    (error.status === 401 && error.code === "unauthenticated")
    || (error.status === 404 && error.code === "not_found")
    || (error.status === 409 && ["conflict", "saved_group_changed", "group_assignment_changed"].includes(error.code))
    || (error.status === 422 && (error.code === "file_malformed" || error.code === "validation_error"))
    || (error.status === 429 && error.code === "rate_limited")
  );
}
