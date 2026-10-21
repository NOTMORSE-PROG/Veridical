// V-081 (F1, screens 4b/4e): saved assignment and source proposal have
// separate entry points and provenance. Viewing a record never confirms it.
import { useEffect, useRef, useState } from "react";
import { Modal, ModalBackdrop } from "../components/Modal";
import type { ConfirmGroupResponse, CurrentGroup, TitlePageProposal } from "../api/types";
import { GroupProposalFields } from "./GroupProposalFields";
import { useGroupProposal, useManuscriptGroup } from "./useCheckRun";

interface GroupProposalDialogProps {
  manuscriptId: number;
  onClose: () => void;
  onDone: (result: ConfirmGroupResponse | null) => void;
}

const EMPTY_PROPOSAL: TitlePageProposal = {
  title: null, short_name: null, members: [], program: null, adviser: null, extraction_failed: false,
};
const secondary = "flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45";
const primary = "flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60";

export function GroupProposalDialog({ manuscriptId, onClose, onDone }: GroupProposalDialogProps) {
  const current = useManuscriptGroup(manuscriptId);
  const [mode, setMode] = useState<"details" | "change" | "source">("details");
  // Keep the edit's expected assignment stable across background refreshes.
  const [editingGroup, setEditingGroup] = useState<CurrentGroup | null>(null);
  const proposal = useGroupProposal(manuscriptId, mode === "source");
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const group = current.data?.group;
  useEffect(() => { contentRef.current?.focus(); }, [mode, current.isSuccess, proposal.isSuccess]);

  const title = mode === "details" ? "Group details"
    : mode === "change" ? "Change group assignment" : "Review proposed group";
  function handleDone(result: ConfirmGroupResponse | null) {
    if (result) onDone(result);
    else setMode("details");
  }
  function startChange() {
    setEditingGroup(current.data?.group ?? null);
    setMode("change");
  }
  return (
    <ModalBackdrop>
      <Modal title={title} onClose={onClose} size="lg" footer={
        mode === "details" ? <>
          <button type="button" onClick={onClose} className={secondary}>Close</button>
          {current.data && <button type="button" onClick={startChange} className={primary}>Change group assignment</button>}
        </> : <div ref={setActionsTarget} />
      }>
        <div ref={contentRef} tabIndex={-1} className="flex flex-col gap-3">
          {current.isPending && <p role="status" className="text-sm text-ink-secondary">Loading saved group details.</p>}
          {current.isError && <p role="alert" className="text-sm text-danger">Could not load saved group details. Close and reopen this dialog to try again.</p>}
          {current.data && mode === "details" && <>
            <p className="text-sm text-ink-secondary">This manuscript is assigned to the group below. These details come from its saved group record.</p>
            {group ? <dl className="flex flex-col gap-3 text-sm text-ink">
              <div><dt className="font-bold">Group name</dt><dd className="break-words">{group.name}</dd></div>
              <div><dt className="font-bold">Program</dt><dd>{group.program ?? "Not set"}</dd></div>
              <div><dt className="font-bold">Group members</dt><dd>{group.member_names.length ? <ul>{group.member_names.map((name, i) => <li key={i} className="break-words">{name}</li>)}</ul> : "No members recorded."}</dd></div>
              {group.title && <div><dt className="font-bold">Saved title</dt><dd className="break-words">{group.title}</dd></div>}
            </dl> : <p className="text-sm text-ink">No saved group assignment is recorded.</p>}
          </>}
          {current.data && mode === "change" && <>
            <button type="button" className="w-fit text-sm font-medium text-link underline hover:text-link-hover" onClick={() => setMode("source")}>Review title-page proposal</button>
            {editingGroup ? <GroupProposalFields key="current" manuscriptId={manuscriptId} proposal={EMPTY_PROPOSAL} currentGroup={editingGroup} expectedGroupId={editingGroup.id} onDone={handleDone} actionsTarget={actionsTarget} /> : <button type="button" className={secondary} onClick={() => setMode("source")}>Enter a group assignment</button>}
          </>}
          {current.data && mode === "source" && <>
            {proposal.isPending && <p role="status" className="text-sm text-ink-secondary">Loading what VERIDICAL read from this manuscript's title page.</p>}
            {proposal.isError && <>
              <p role="alert" className="text-sm text-danger">Could not load the title-page proposal. The saved group details are still available.</p>
              <button type="button" className={secondary} onClick={() => setMode("details")}>Back to group details</button>
            </>}
            {proposal.data && <GroupProposalFields key="source" manuscriptId={manuscriptId} proposal={proposal.data} expectedGroupId={editingGroup?.id ?? null} onDone={handleDone} actionsTarget={actionsTarget} />}
          </>}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}
