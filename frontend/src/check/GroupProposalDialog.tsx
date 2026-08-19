// V-063 (AC6) — the "reopen later" entry point. Re-derives the SAME
// proposal UploadManuscriptModal showed right after ingest (the backend
// recomputes it from the stored raw extraction, nothing new is
// persisted) so skipping the confirm step at upload time is never a dead
// end -- the exact same form is always one click away from the
// dashboard.
import { Modal, ModalBackdrop } from "../components/Modal";
import type { ConfirmGroupResponse } from "../api/types";
import { GroupProposalFields } from "./GroupProposalFields";
import { useGroupProposal } from "./useCheckRun";

interface GroupProposalDialogProps {
  manuscriptId: number;
  onClose: () => void;
  onDone: (result: ConfirmGroupResponse | null) => void;
}

export function GroupProposalDialog({ manuscriptId, onClose, onDone }: GroupProposalDialogProps) {
  const proposal = useGroupProposal(manuscriptId);

  return (
    <ModalBackdrop>
      <Modal title="Confirm group" onClose={onClose} size="lg">
        {proposal.isPending && (
          <p role="status" className="text-sm text-ink-secondary">
            Loading what VERIDICAL read from this manuscript's title page.
          </p>
        )}
        {proposal.isError && (
          <p role="alert" className="text-sm font-medium text-danger">
            Could not load this manuscript's group proposal. Check your connection and try again.
          </p>
        )}
        {proposal.data && (
          <GroupProposalFields manuscriptId={manuscriptId} proposal={proposal.data} onDone={onDone} />
        )}
      </Modal>
    </ModalBackdrop>
  );
}
