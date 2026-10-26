// F1, screen4e: saved assignment is read separately from assignment intent.
import { useEffect, useRef, useState } from "react";
import { Modal, ModalBackdrop } from "../components/Modal";
import type { GroupAssignmentOutput } from "../api/groupAssignmentOutcome";
import { GroupAssignmentFields } from "./GroupAssignmentFields";
import { groupPrimary, groupSecondary, SavedGroupFacts } from "./SavedGroupFields";
import { useManuscriptGroup } from "./useCheckRun";

interface GroupProposalDialogProps {
  manuscriptId: number;
  onClose: () => void;
  onDone: (result: GroupAssignmentOutput | null) => void;
}

export function GroupProposalDialog({ manuscriptId, onClose, onDone }: GroupProposalDialogProps) {
  const current = useManuscriptGroup(manuscriptId);
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(null);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => { content.current?.focus(); }, [changing, current.isSuccess]);
  return <ModalBackdrop>
    <Modal title={changing ? "Change group assignment" : "Group details"} onClose={busy ? undefined : onClose} size="lg" footer={changing
      ? <div ref={setActionsTarget} />
      : <><button type="button" onClick={onClose} className={groupSecondary}>Close</button>
        {current.data && !current.isFetching && !current.isError && <button type="button" onClick={() => setChanging(true)} className={groupPrimary}>Change group assignment</button>}</>}>
      <div ref={content} tabIndex={-1} className="flex flex-col gap-3">
        {!changing && <>
          {current.isPending && <p role="status" className="text-sm text-ink-secondary">Loading saved group details.</p>}
          {current.isError && <div role="alert" className="flex flex-col gap-2 text-sm text-danger"><p>Could not load saved group details.</p><button type="button" onClick={() => { void current.refetch(); }} className="w-fit font-bold underline">Try loading saved group details again</button></div>}
          {current.data && !current.isError && <>{current.data.group
            ? <><p className="text-sm text-ink-secondary">This manuscript is assigned to the group below. These details come from its saved group record.</p><SavedGroupFacts group={current.data.group} /></>
            : <p className="text-sm text-ink">No saved group assignment is recorded.</p>}</>}
        </>}
        {changing && <GroupAssignmentFields key={manuscriptId} manuscriptId={manuscriptId} changing actionsTarget={actionsTarget} onBusyChange={setBusy} onDone={(result) => {
          if (result) onDone(result);
          else setChanging(false);
        }} />}
      </div>
    </Modal>
  </ModalBackdrop>;
}
