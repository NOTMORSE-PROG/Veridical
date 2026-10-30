// V-063 -- the auto-proposed group confirm form (design review spec,
// 2026-08-19). Presentational only, no Modal chrome of its own, so
// UploadManuscriptModal (opened right after upload) and
// GroupProposalDialog (reopened later, AC6) render the byte-identical
// form -- "dismiss now, decide later" only stays honest if both paths
// show the exact same thing.
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api/client";
import { AnchorPill } from "../components/AnchorPill";
import { useGroupCollisionCheck, usePrograms } from "../dashboard/useDashboard";
import type { ConfirmGroupResponse, CurrentGroup, TitlePageProposal } from "../api/types";
import type { GroupProposalInput } from "../api/groupAssignmentOutcome";
import { fieldRefusal, formRefusal, isValidationRefusal, REFUSAL_NOTHING_CHANGED } from "../api/fieldRefusal";
import { GROUP_REQUEST_REFUSED } from "./groupRefusalCopy";

export interface GroupProposalController {
  isPending: boolean;
  isSuccess: boolean;
  data?: ConfirmGroupResponse;
  error?: unknown;
  mutate: (body: GroupProposalInput) => void;
}

function RemoveIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <line x1="5" y1="5" x2="19" y2="19" />
      <line x1="19" y1="5" x2="5" y2="19" />
    </svg>
  );
}

function MergeIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 3v8a4 4 0 0 0 4 4h8" />
      <path d="M15 11l3 4-3 4" />
    </svg>
  );
}

function PlusCircleIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v8M8 12h8" />
    </svg>
  );
}

function normalizeName(value: string) {
  return value.replace(/[ \t\n\r\f\v]+/g, " ").replace(/^ +| +$/g, "").toLowerCase();
}

function memberIdentity(names: string[]) {
  return [...new Set(names.filter((name) => name.trim()).map(normalizeName))].sort();
}

interface MemberField {
  id: number;
  value: string;
  anchor: string | null;
  edited: boolean;
}

let memberFieldSeq = 0;

/** V-080: the form keeps its handlers while a task sheet can place its one
 * action set in a footer outside the scrolling content. */
export function ProposalActions({ target, children }: { target?: HTMLElement | null; children: ReactNode }) {
  const actions = <div className="signal-group-proposal__actions">{children}</div>;
  return target ? createPortal(actions, target) : actions;
}
function newMemberField(value: string, anchor: string | null): MemberField {
  memberFieldSeq += 1;
  return { id: memberFieldSeq, value, anchor, edited: anchor === null };
}

export function GroupProposalFields({
  proposal,
  onDone,
  actionsTarget,
  currentGroup,
  expectedGroupId,
  confirm,
  active = true,
  onUseSaved,
  manualEntry = false,
  cancelLabel,
}: {
  manuscriptId: number;
  proposal: TitlePageProposal;
  /** `null` = the instructor skipped without confirming anything. */
  onDone: (result: ConfirmGroupResponse | null) => void;
  actionsTarget?: HTMLElement | null;
  currentGroup?: CurrentGroup;
  expectedGroupId: number | null;
  confirm: GroupProposalController;
  active?: boolean;
  onUseSaved?: () => void;
  manualEntry?: boolean;
  cancelLabel?: string;
}) {
  const { data: programs } = usePrograms();
  const programFieldId = useId();

  const [groupName, setGroupName] = useState(currentGroup?.name ?? proposal.short_name?.value ?? "");
  const [groupNameEdited, setGroupNameEdited] = useState(currentGroup !== undefined || proposal.short_name === null);
  // BUG-551: the members that were sent, in order, and which refused fields
  // have been changed since.
  const sentMemberIds = useRef<number[]>([]);
  const memberInputRefs = useRef(new Map<number, HTMLInputElement>());
  const [editedSinceRefusal, setEditedSinceRefusal] = useState<ReadonlySet<string>>(() => new Set());
  const groupNameRefusal = editedSinceRefusal.has("group_name") ? null : fieldRefusal(confirm.error, "group_name");
  const memberRefusals = new Map<number, string>();
  // Whether any refused name has a field on this form, edited since or not.
  let namesPlaced = groupNameRefusal !== null || (isValidationRefusal(confirm.error) && confirm.error.fieldMessages.has("group_name"));
  if (isValidationRefusal(confirm.error)) {
    for (const [field, message] of confirm.error.fieldMessages) {
      const match = /^member_names\.(\d+)$/.exec(field);
      const id = match ? sentMemberIds.current[Number(match[1])] : undefined;
      if (id === undefined) continue;
      namesPlaced = true;
      if (!editedSinceRefusal.has(`member:${id}`)) memberRefusals.set(id, message);
    }
  }
  const firstRefusal = groupNameRefusal ? "group_name" : [...memberRefusals.keys()][0] ?? null;
  useEffect(() => {
    if (firstRefusal === null) return;
    (firstRefusal === "group_name" ? groupNameRef.current : memberInputRefs.current.get(firstRefusal))?.focus();
    // Only when a refusal arrives, not as the instructor edits.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [confirm.error]);
  const [members, setMembers] = useState<MemberField[]>(() =>
    currentGroup ? currentGroup.member_names.map((name) => newMemberField(name, null))
      : proposal.members.map((m) => newMemberField(m.value, m.anchor)),
  );
  const [programId, setProgramId] = useState<number | null>(currentGroup?.program_id ?? null);
  const [programEdited, setProgramEdited] = useState(false);
  const [attempted, setAttempted] = useState(false);

  const groupNameRef = useRef<HTMLInputElement>(null);
  const bannerRef = useRef<HTMLDivElement>(null);
  const addMemberRef = useRef<HTMLButtonElement>(null);
  const removeButtonRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  const [pendingFocus, setPendingFocus] = useState<"add" | number | null>(null);
  const groupNameId = useId();
  const groupNameErrId = useId();

  // Resolve the proposed program's NAME to an id once the program list
  // has loaded -- the proposal only ever carries a name (what the degree
  // line said), never an id.
  useEffect(() => {
    if (currentGroup || !programs || proposal.program === null || programEdited) return;
    const match = programs.find((p) => p.name === proposal.program!.value);
    if (match) setProgramId(match.id);
    // BUG-086: deliberately fires only when the program LIST changes --
    // `proposal.program`/`programEdited` are read as a one-shot gate at
    // that moment (correct, since closures capture the render's live
    // value whenever this effect actually re-fires); listing them would
    // re-run this resolution on every keystroke of an unrelated field.
    // The OLD `eslint-disable` comment here suppressed nothing (eslint
    // isn't installed; oxlint is, and DOES enforce this rule -- confirmed
    // live). This is oxlint's own real directive syntax, verified to
    // suppress it.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [programs]);

  useEffect(() => {
    if (confirm.isSuccess) bannerRef.current?.focus();
  }, [confirm.isSuccess]);

  // Owner's call (2026-08-19), after backend review's review: rule 3 of
  // the matching rule (ticket's DECIDED section) says a same-short-name-
  // zero-overlap collision must be PROPOSED to the instructor, not
  // silently applied -- this is what surfaces that proposal WHILE they're
  // still editing, instead of only disclosing it after confirming via a
  // disambiguating suffix. Debounced (same manual setTimeout pattern
  // `RerunModal.tsx` already uses) so typing a member name doesn't fire
  // the check on every keystroke.
  const [debouncedCollisionCheck, setDebouncedCollisionCheck] = useState({
    name: groupName,
    members: members.map((m) => m.value),
  });
  useEffect(() => {
    const handle = setTimeout(() => {
      setDebouncedCollisionCheck({ name: groupName, members: members.map((m) => m.value) });
    }, 400);
    return () => clearTimeout(handle);
  }, [groupName, members]);
  const collisionCheck = useGroupCollisionCheck(
    debouncedCollisionCheck.name,
    debouncedCollisionCheck.members,
    currentGroup?.id,
  );
  const showCollisionWarning = collisionCheck.data?.collision === true && !confirm.isSuccess;
  const unchanged = currentGroup !== undefined
    && normalizeName(groupName) === normalizeName(currentGroup.name)
    && JSON.stringify(memberIdentity(members.map((m) => m.value))) === JSON.stringify(memberIdentity(currentGroup.member_names))
    && programId === currentGroup.program_id;

  // UX review (V-063 review): removing a member row used to strand focus
  // at <body> -- BUG-054's same "a control that unmounts mid-interaction
  // must hand focus somewhere real" rule, recurring here in a brand-new
  // component. Deferred to an effect because the target (the next
  // member's remove button, or "Add member" if none remain) only exists
  // in the DOM after `members` has re-rendered.
  useEffect(() => {
    if (pendingFocus === null) return;
    if (pendingFocus === "add") {
      addMemberRef.current?.focus();
    } else {
      removeButtonRefs.current.get(pendingFocus)?.focus();
    }
    setPendingFocus(null);
  }, [pendingFocus, members]);

  const invalid = attempted && groupName.trim() === "";
  const proposedProgramUnmatched =
    !programEdited &&
    proposal.program !== null &&
    programs !== undefined &&
    !programs.some((p) => p.name === proposal.program!.value);

  function updateMember(id: number, value: string) {
    setMembers((prev) => prev.map((m) => (m.id === id ? { ...m, value, edited: true } : m)));
  }

  function removeMember(id: number) {
    setMembers((prev) => {
      const idx = prev.findIndex((m) => m.id === id);
      const next = prev.filter((m) => m.id !== id);
      setPendingFocus(next.length === 0 ? "add" : next[Math.min(idx, next.length - 1)].id);
      return next;
    });
  }

  function addMember() {
    setMembers((prev) => [...prev, newMemberField("", null)]);
  }

  function clearAll() {
    setGroupName("");
    setGroupNameEdited(true);
    setMembers([]);
    setProgramId(null);
    setProgramEdited(true);
    groupNameRef.current?.focus();
  }

  function handleConfirm() {
    setAttempted(true);
    if (!active || confirm.isPending || unchanged) return;
    const trimmedName = groupName.trim();
    if (trimmedName === "") {
      groupNameRef.current?.focus();
      return;
    }
    const sent = members.filter((m) => m.value.trim().length > 0);
    const memberNames = sent.map((m) => m.value.trim());
    // Blank members are not sent, so the server's member_names.N counts only
    // these; keep which on-screen member each one was.
    sentMemberIds.current = sent.map((m) => m.id);
    setEditedSinceRefusal(new Set());
    // Deliberately no onSuccess callback here -- `onDone` fires only when
    // the instructor clicks "Done" on the terminal banner below, so they
    // always see WHICH outcome happened (matched vs created) before this
    // form goes away.
    confirm.mutate({
      group_name: trimmedName,
      member_names: memberNames,
      program_id: programId,
      // V-066: read-only in this form (not an editable field here), so
      // whatever VERIDICAL extracted is exactly what's sent -- null when
      // nothing was found, same as every other unfound proposal field.
      title: currentGroup ? currentGroup.title : proposal.title?.value ?? null,
      expected_group_id: expectedGroupId,
    });
  }

  // A refusal of a name is shown at that name; the form's own line is for
  // anything else that went wrong.
  const serverError = namesPlaced ? null
    : isValidationRefusal(confirm.error) ? formRefusal(confirm.error, [], GROUP_REQUEST_REFUSED)
    : confirm.error instanceof ApiError
      ? confirm.error.message
      : confirm.error
        ? "Could not save this group. Check your connection and try again."
        : null;

  if (confirm.isSuccess && confirm.data) {
    const result = confirm.data;
    return (
      <div hidden={!active} inert={!active} className={active ? "flex flex-col gap-3" : "hidden"}>
        <div
          ref={bannerRef}
          tabIndex={-1}
          role="status"
          className={
            result.matched
              ? "flex items-center gap-2 rounded-md bg-status-info-bg px-4 py-3 text-sm text-status-info-text"
              : "flex items-center gap-2 rounded-md bg-status-success-bg px-4 py-3 text-sm text-status-success-text"
          }
        >
          {result.matched ? <MergeIcon /> : <PlusCircleIcon />}
          {result.outcome === "unchanged" ? (
            <>Assignment unchanged: <b>{result.group_label}</b>.</>
          ) : result.matched ? (
            <>
              Assigned to an existing group: <b>{result.group_label}</b>. The submitted name
              matches a name recorded for this group, and at least one submitted member name
              matches its records.
            </>
          ) : (
            <>
              Created a new group: <b>{result.group_label}</b>.
            </>
          )}
        </div>
        <dl className="flex flex-col gap-2 text-sm text-ink">
          <div><dt className="font-bold">Assigned group</dt><dd className="break-words">{result.group_label}</dd></div>
          <div><dt className="font-bold">Saved program</dt><dd>{result.program ?? "Not set"}</dd></div>
          <div><dt className="font-bold">Saved title</dt><dd className="break-words">{result.title ?? "Not recorded"}</dd></div>
        </dl>
        {active && <ProposalActions target={actionsTarget}>
          <button
            type="button"
            onClick={() => onDone(result)}
            className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
          >
            Done
          </button>
        </ProposalActions>}
      </div>
    );
  }

  return (
    <div hidden={!active} inert={!active} className={active ? "signal-group-proposal" : "hidden"}>
      {currentGroup ? (
        <p className="text-sm text-ink-secondary">
          These fields start from the current group record. Changing them can assign this manuscript
          to another group or create a separate group. Other manuscripts are not edited here.
        </p>
      ) : manualEntry ? (
        <p className="text-sm text-ink-secondary">Enter the group details for this manuscript.</p>
      ) : proposal.extraction_failed ? (
        <p role="status" className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
          Group details could not be extracted. Enter them here, or use a saved group.
        </p>
      ) : (
        <p className="text-sm text-ink-secondary">
          These details were read from the manuscript's title page. Check them before assigning
          this manuscript to a group.
        </p>
      )}

      <p className="text-sm text-ink-secondary">
        The program and title shown here are saved only when a new group is created.
        Matching an existing group keeps its saved program and title unchanged.
      </p>

      {(currentGroup?.title || (!currentGroup && proposal.title)) && (
        <p className="text-sm text-ink-tertiary break-words">
          {currentGroup ? "Saved title" : "Extracted title"}: <span className="text-ink">{currentGroup ? currentGroup.title : proposal.title?.value}</span>{" "}
          {!currentGroup && proposal.title && <AnchorPill anchor={proposal.title.anchor} />}
        </p>
      )}

      <div className="signal-group-proposal__identity" data-has-program={Boolean(programs?.length)}>
        <div className="signal-group-proposal__field">
          <label htmlFor={groupNameId} className="text-sm font-medium text-ink">
            Group name
          </label>
          <input
            ref={groupNameRef}
            id={groupNameId}
            type="text"
            value={groupName}
            maxLength={200}
            placeholder="e.g. VERIDICAL"
            disabled={confirm.isPending}
            aria-invalid={invalid || groupNameRefusal ? "true" : undefined}
            aria-describedby={invalid ? groupNameErrId : groupNameRefusal ? `${groupNameErrId}-refusal` : undefined}
            onChange={(event) => {
              setGroupName(event.target.value);
              setGroupNameEdited(true);
              setEditedSinceRefusal((current) => new Set([...current, "group_name"]));
            }}
            className={`min-h-11 w-full rounded-md border bg-panel px-3 text-base text-ink ${invalid || groupNameRefusal ? "border-2 border-status-attention-text" : "border-border-input"}`}
          />
          {currentGroup ? null : !groupNameEdited && proposal.short_name ? (
            <AnchorPill anchor={proposal.short_name.anchor} />
          ) : groupNameEdited && proposal.short_name ? (
            <span className="text-xs text-ink-tertiary italic">Edited.</span>
          ) : !proposal.short_name ? (
            <span className="text-xs text-ink-tertiary">Not found on the title page. Enter it yourself.</span>
          ) : null}
          {invalid && (
            <p id={groupNameErrId} className="text-sm text-status-attention-text">
              Enter a group name before confirming.
            </p>
          )}
          {!invalid && groupNameRefusal && (
            <p id={`${groupNameErrId}-refusal`} className="signal-field-error">{groupNameRefusal} {REFUSAL_NOTHING_CHANGED}</p>
          )}
        </div>

        {programs && programs.length > 0 && (
          <div className="signal-group-proposal__field">
            <label htmlFor={programFieldId} className="text-sm font-medium text-ink">Program</label>
            <select
              id={programFieldId}
              value={programId ?? ""}
              disabled={confirm.isPending}
              onChange={(event) => {
                setProgramId(event.target.value === "" ? null : Number(event.target.value));
                setProgramEdited(true);
              }}
              className="min-h-11 w-full rounded-md border border-border-input bg-panel px-3 text-base text-ink"
            >
              <option value="">Not set</option>
              {programs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {currentGroup ? null : programEdited ? (
              <span className="text-xs text-ink-tertiary italic">Entered manually.</span>
            ) : proposal.program === null ? (
              <span className="text-xs text-ink-tertiary">Not found on the title page.</span>
            ) : proposedProgramUnmatched ? (
              <p className="text-xs text-ink-tertiary">
                VERIDICAL read the degree as "{proposal.program.value}" but that isn't a program
                you've set up. Choose one, or leave as Not set.
              </p>
            ) : (
              <AnchorPill anchor={proposal.program.anchor} />
            )}
          </div>
        )}
      </div>

      <div className="signal-group-proposal__field">
        <span className="text-sm font-medium text-ink">Group members</span>
        {members.length === 0 && (
          <p className="text-xs text-ink-tertiary">{currentGroup ? "No members recorded." : "No members found on the title page."}</p>
        )}
        {members.map((m, i) => (
          <div key={m.id} className="signal-group-proposal__member">
            <input
              ref={(el) => { if (el) memberInputRefs.current.set(m.id, el); else memberInputRefs.current.delete(m.id); }}
              type="text"
              value={m.value}
              maxLength={200}
              disabled={confirm.isPending}
              aria-label={`Group member ${i + 1} name`}
              aria-invalid={memberRefusals.has(m.id) ? "true" : undefined}
              aria-describedby={memberRefusals.has(m.id) ? `${groupNameErrId}-member-${m.id}` : undefined}
              onChange={(event) => { updateMember(m.id, event.target.value); setEditedSinceRefusal((current) => new Set([...current, `member:${m.id}`])); }}
              className={`h-11 rounded-md border bg-panel px-3 text-base text-ink ${memberRefusals.has(m.id) ? "border-2 border-status-attention-text" : "border-border-input"}`}
            />
            {memberRefusals.has(m.id) && <p id={`${groupNameErrId}-member-${m.id}`} className="signal-field-error signal-group-proposal__member-error">{memberRefusals.get(m.id)} {REFUSAL_NOTHING_CHANGED}</p>}
            <span className="signal-group-proposal__member-note">
              {!m.edited && m.anchor ? (
                <AnchorPill anchor={m.anchor} />
              ) : (
                <span className="text-xs whitespace-nowrap text-ink-tertiary italic">
                  {m.anchor ? "Edited" : "Added manually"}
                </span>
              )}
            </span>
            <button
              type="button"
              ref={(el) => {
                if (el) removeButtonRefs.current.set(m.id, el);
                else removeButtonRefs.current.delete(m.id);
              }}
              disabled={confirm.isPending}
              aria-label={`Remove ${m.value || "this member"}`}
              onClick={() => removeMember(m.id)}
              className="flex h-11 w-11 flex-none items-center justify-center rounded-sm text-ink-tertiary hover:bg-status-neutral-bg hover:text-ink disabled:opacity-45"
            >
              <RemoveIcon />
            </button>
          </div>
        ))}
        <button
          type="button"
          ref={addMemberRef}
          disabled={confirm.isPending}
          onClick={addMember}
          className="flex h-11 w-fit items-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink hover:bg-status-neutral-bg disabled:opacity-45"
        >
          Add member
        </button>
      </div>

      {!currentGroup && proposal.adviser && (
        <p className="text-xs text-ink-tertiary">
          Adviser: {proposal.adviser.value} <AnchorPill anchor={proposal.adviser.anchor} />
        </p>
      )}

      {showCollisionWarning && (
        <p role="status" className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
          A group named "{collisionCheck.data?.existing_group_name}" already exists, but none of
          these members match its records. Confirming will create this as a separate, new group.
          If this is actually the same team, check the member names for a typo or a naming
          difference before confirming.
          {onUseSaved && <> <button type="button" disabled={confirm.isPending} onClick={onUseSaved} className="font-bold underline">Use a saved group</button></>}
        </p>
      )}

      {!currentGroup && !proposal.extraction_failed && (groupName || members.length > 0 || programId !== null) && (
        <button
          type="button"
          disabled={confirm.isPending}
          onClick={clearAll}
          className="w-fit text-sm font-medium text-link underline hover:text-link-hover disabled:opacity-45"
        >
          None of this is right. Clear and enter manually.
        </button>
      )}

      {serverError && (
        <p role="alert" className="text-sm font-medium text-status-attention-text">
          <span className="sr-only">Error: </span>
          {serverError}
        </p>
      )}

      {active && <ProposalActions target={actionsTarget}>
        <button
          type="button"
          disabled={confirm.isPending}
          onClick={() => onDone(null)}
          className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
        >
          {cancelLabel ?? (currentGroup ? "Cancel" : "Skip for now")}
        </button>
        <button
          type="button"
          disabled={confirm.isPending || unchanged}
          aria-busy={confirm.isPending ? "true" : undefined}
          onClick={handleConfirm}
          className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60"
        >
          {confirm.isPending ? "Confirming." : "Confirm assignment"}
        </button>
      </ProposalActions>}
    </div>
  );
}
