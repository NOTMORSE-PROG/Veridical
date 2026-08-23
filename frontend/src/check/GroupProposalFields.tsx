// V-063 -- the auto-proposed group confirm form (ui-designer spec,
// 2026-08-19). Presentational only, no Modal chrome of its own, so
// UploadManuscriptModal (opened right after upload) and
// GroupProposalDialog (reopened later, AC6) render the byte-identical
// form -- "dismiss now, decide later" only stays honest if both paths
// show the exact same thing.
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { AnchorPill } from "../components/AnchorPill";
import { useGroupCollisionCheck, usePrograms } from "../dashboard/useDashboard";
import type { ConfirmGroupResponse, TitlePageProposal } from "../api/types";
import { useConfirmGroup } from "./useCheckRun";

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

interface MemberField {
  id: number;
  value: string;
  anchor: string | null;
  edited: boolean;
}

let memberFieldSeq = 0;
function newMemberField(value: string, anchor: string | null): MemberField {
  memberFieldSeq += 1;
  return { id: memberFieldSeq, value, anchor, edited: anchor === null };
}

export function GroupProposalFields({
  manuscriptId,
  proposal,
  onDone,
}: {
  manuscriptId: number;
  proposal: TitlePageProposal;
  /** `null` = the instructor skipped without confirming anything. */
  onDone: (result: ConfirmGroupResponse | null) => void;
}) {
  const confirm = useConfirmGroup(manuscriptId);
  const { data: programs } = usePrograms();

  const [groupName, setGroupName] = useState(proposal.short_name?.value ?? "");
  const [groupNameEdited, setGroupNameEdited] = useState(proposal.short_name === null);
  const [members, setMembers] = useState<MemberField[]>(() =>
    proposal.members.map((m) => newMemberField(m.value, m.anchor)),
  );
  const [programId, setProgramId] = useState<number | null>(null);
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
    if (!programs || proposal.program === null || programEdited) return;
    const match = programs.find((p) => p.name === proposal.program!.value);
    if (match) setProgramId(match.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [programs]);

  useEffect(() => {
    if (confirm.isSuccess) bannerRef.current?.focus();
  }, [confirm.isSuccess]);

  // Owner's call (2026-08-19), after backend-critic's review: rule 3 of
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
  );
  const showCollisionWarning = collisionCheck.data?.collision === true && !confirm.isSuccess;

  // ux-critic (V-063 review): removing a member row used to strand focus
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
    const trimmedName = groupName.trim();
    if (trimmedName === "") {
      groupNameRef.current?.focus();
      return;
    }
    const memberNames = members.map((m) => m.value.trim()).filter((v) => v.length > 0);
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
      title: proposal.title?.value ?? null,
    });
  }

  const serverError =
    confirm.error instanceof ApiError
      ? confirm.error.message
      : confirm.error
        ? "Could not save this group. Check your connection and try again."
        : null;

  if (confirm.isSuccess && confirm.data) {
    const result = confirm.data;
    return (
      <div className="flex flex-col gap-3">
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
          {result.matched ? (
            <>
              Matched to an existing group: <b>{result.group_label}</b>. VERIDICAL found a group
              with the same name and at least one shared member, so this manuscript joined that
              group's history.
            </>
          ) : (
            <>
              Created a new group: <b>{result.group_label}</b>.
            </>
          )}
        </div>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => onDone(result)}
            className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover sm:h-9"
          >
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {proposal.extraction_failed ? (
        <p role="status" className="rounded-md bg-status-caution-bg px-3 py-2 text-sm text-status-caution-text">
          Couldn't read the title page. This can happen with a scanned image with no extractable
          text. Enter the group details yourself.
        </p>
      ) : (
        <p className="text-sm text-ink-secondary">
          VERIDICAL read this from the manuscript's title page. Check it before it becomes the
          group record.
        </p>
      )}

      {proposal.title && (
        <p className="text-sm text-ink-tertiary break-words">
          Extracted title: <span className="text-ink">{proposal.title.value}</span>{" "}
          <AnchorPill anchor={proposal.title.anchor} />
        </p>
      )}

      <div className="flex flex-col gap-1.5">
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
          aria-invalid={invalid ? "true" : undefined}
          aria-describedby={invalid ? groupNameErrId : undefined}
          onChange={(event) => {
            setGroupName(event.target.value);
            setGroupNameEdited(true);
          }}
          className={`min-h-11 w-full rounded-md border bg-panel px-3 text-base text-ink sm:h-9 ${invalid ? "border-2 border-status-attention-text" : "border-border-input"}`}
        />
        {!groupNameEdited && proposal.short_name ? (
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
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-sm font-medium text-ink">Group members</span>
        {members.length === 0 && (
          <p className="text-xs text-ink-tertiary">No members found on the title page.</p>
        )}
        {members.map((m, i) => (
          <div key={m.id} className="flex items-center gap-2">
            <input
              type="text"
              value={m.value}
              maxLength={200}
              disabled={confirm.isPending}
              aria-label={`Group member ${i + 1} name`}
              onChange={(event) => updateMember(m.id, event.target.value)}
              className="h-11 flex-1 rounded-md border border-border-input bg-panel px-3 text-base text-ink sm:h-9"
            />
            {!m.edited && m.anchor ? (
              <AnchorPill anchor={m.anchor} />
            ) : (
              <span className="text-xs whitespace-nowrap text-ink-tertiary italic">
                {m.anchor ? "Edited" : "Added manually"}
              </span>
            )}
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
          className="flex h-11 w-fit items-center rounded-md border border-border-input bg-panel px-3.5 text-sm font-medium text-ink hover:bg-status-neutral-bg disabled:opacity-45 sm:h-9"
        >
          Add member
        </button>
      </div>

      {programs && programs.length > 0 && (
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-ink">Program</label>
          <select
            value={programId ?? ""}
            disabled={confirm.isPending}
            onChange={(event) => {
              setProgramId(event.target.value === "" ? null : Number(event.target.value));
              setProgramEdited(true);
            }}
            className="h-9 w-fit rounded-md border border-border-input bg-panel px-2.5 text-sm text-ink"
          >
            <option value="">Not set</option>
            {programs.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          {programEdited ? (
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

      {proposal.adviser && (
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
        </p>
      )}

      {!proposal.extraction_failed && (groupName || members.length > 0 || programId !== null) && (
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

      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          disabled={confirm.isPending}
          onClick={() => onDone(null)}
          className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45 sm:h-9"
        >
          Skip for now
        </button>
        <button
          type="button"
          disabled={confirm.isPending}
          aria-busy={confirm.isPending ? "true" : undefined}
          onClick={handleConfirm}
          className="flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60 sm:h-9"
        >
          {confirm.isPending ? "Confirming." : "Confirm group"}
        </button>
      </div>
    </div>
  );
}
