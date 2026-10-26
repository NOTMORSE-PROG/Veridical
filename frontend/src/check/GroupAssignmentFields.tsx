// F1, screens4a/4e: both upload and reassignment use this explicit intent boundary.
import { useEffect, useId, useRef, useState } from "react";
import type { GroupAssignmentOutput, SavedGroup } from "../api/groupAssignmentOutcome";
import { validateManuscriptGroup } from "../api/groupAssignmentOutcome";
import type { ConfirmGroupResponse, ManuscriptGroup, TitlePageProposal } from "../api/types";
import { GroupProposalFields, ProposalActions, type GroupProposalController } from "./GroupProposalFields";
import { groupPrimary, groupSecondary, SavedGroupFacts, SavedGroupFields } from "./SavedGroupFields";
import { useGroupProposal, useManuscriptGroup } from "./useCheckRun";
import { useGroupAssignment } from "./useGroupAssignment";

const EMPTY: TitlePageProposal = { title: null, short_name: null, members: [], program: null, adviser: null, extraction_failed: false };
function hasSourceFields(proposal: TitlePageProposal) {
  return proposal.extraction_failed || proposal.title !== null || proposal.short_name !== null
    || proposal.members.length > 0 || proposal.program !== null || proposal.adviser !== null;
}

export function GroupAssignmentFields({ manuscriptId, initialProposal, actionsTarget, onDone, onBusyChange, changing = false }: {
  manuscriptId: number; initialProposal?: TitlePageProposal; actionsTarget?: HTMLElement | null;
  onDone: (result: GroupAssignmentOutput | null) => void; onBusyChange?: (busy: boolean) => void;
  changing?: boolean;
}) {
  const assignment = useGroupAssignment(manuscriptId);
  const current = useManuscriptGroup(manuscriptId);
  const [baseline, setBaseline] = useState<ManuscriptGroup | undefined>();
  const [intent, setIntent] = useState<"saved" | "proposal" | null>(null);
  const [sourceRequested, setSourceRequested] = useState(false);
  const [source, setSource] = useState(initialProposal);
  const [useSource, setUseSource] = useState(initialProposal !== undefined);
  const proposal = useGroupProposal(manuscriptId, sourceRequested);
  const [refreshing, setRefreshing] = useState(false);
  const [readError, setReadError] = useState<string | undefined>();
  const intentName = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const cancelLabel = changing ? "Cancel" : "Skip for now";
  const state = assignment.state;
  const working = state.kind === "saving" || (state.kind === "unknown" && state.checking);
  const ready = assignment.scope.status === "ready";
  const locked = assignment.locked || refreshing || !baseline;

  useEffect(() => {
    if (!baseline && current.isSuccess && !current.isFetching) {
      try { setBaseline(validateManuscriptGroup(current.data, manuscriptId)); }
      catch { setReadError("Could not verify the saved assignment. Try loading it again."); }
    }
  }, [baseline, current.data, current.isFetching, current.isSuccess, manuscriptId]);
  useEffect(() => {
    if (sourceRequested && !source && proposal.isSuccess) {
      setSource(proposal.data); setUseSource(true);
    }
  }, [proposal.data, proposal.isSuccess, source, sourceRequested]);
  useEffect(() => { onBusyChange?.(working); return () => onBusyChange?.(false); }, [onBusyChange, working]);
  useEffect(() => { if (ready && (state.kind === "saved" || state.kind === "unknown" || state.kind === "rejected")) heading.current?.focus(); }, [ready, state.kind]);

  async function refreshCurrent() {
    if (working || !assignment.scope.isCurrent()) return;
    setRefreshing(true); setReadError(undefined);
    try {
      await assignment.scope.verify();
      const result = await current.refetch();
      if (!assignment.scope.isCurrent()) return;
      if (!result.isSuccess) throw new Error();
      const saved = validateManuscriptGroup(result.data, manuscriptId);
      await assignment.scope.verify();
      if (assignment.scope.isCurrent()) { setBaseline(saved); assignment.markRecoveryReviewed("current_assignment"); }
    } catch {
      if (assignment.scope.isCurrent()) setReadError("Could not verify the saved assignment. Try loading it again.");
    } finally { setRefreshing(false); }
  }

  const confirm: GroupProposalController = {
    isPending: locked || (state.kind === "rejected" && state.recovery === "current_assignment"), isSuccess: false,
    mutate: (body) => { if (!locked) void assignment.submit({ kind: "proposal", body }); },
  };
  function assignSaved(reviewed: SavedGroup) {
    if (locked || !baseline) return;
    void assignment.submit({ kind: "saved", reviewed, body: {
      saved_group_id: reviewed.id, fingerprint: reviewed.fingerprint, expected_group_id: baseline.group?.id ?? null,
    } });
  }

  const sessionNotice = !ready && <div className="flex flex-col gap-3 text-sm text-ink">
    <p role={assignment.scope.status === "checking" ? "status" : "alert"}>{assignment.scope.status === "checking"
      ? "Checking your sign-in status."
      : assignment.scope.status === "lost" ? "Your sign-in context changed. Close this form and reopen the manuscript's group details."
        : "Could not confirm your sign-in status. Check your session before continuing."}</p>
    {assignment.scope.status === "unavailable" && <button type="button" className="w-fit font-bold underline" onClick={() => { void assignment.scope.verify().catch(() => {}); }}>Check sign-in status</button>}
    {!working && <ProposalActions target={actionsTarget}><button type="button" onClick={() => onDone(null)} className={groupSecondary}>Close form</button></ProposalActions>}
  </div>;
  if (assignment.scope.status === "lost") return sessionNotice;

  if (state.kind === "saved") {
    const selected = "receipt" in state.result ? state.result : null;
    const matched = selected ? null : state.result as ConfirmGroupResponse;
    return <>{sessionNotice}<div hidden={!ready} inert={!ready} role="status" className={ready ? "flex flex-col gap-3" : "hidden"}>
      <h3 ref={heading} tabIndex={-1} className="text-base font-bold text-ink">{selected
        ? `Assigned to saved group: ${selected.receipt.effective.name}`
        : matched?.outcome === "unchanged" ? `Assignment unchanged: ${matched.group_label}`
          : matched?.outcome === "matched" ? `Assigned to an existing group: ${matched.group_label}`
            : `Created a new group: ${matched?.group_label}`}</h3>
      {selected ? <SavedGroupFacts group={selected.receipt.effective} /> : <dl className="flex flex-col gap-2 text-sm text-ink">
        <div><dt className="font-bold">Saved program</dt><dd>{matched?.program ?? "Not set"}</dd></div>
        <div><dt className="font-bold">Saved title</dt><dd className="break-words">{matched?.title ?? "Not recorded"}</dd></div>
      </dl>}
      {ready && <ProposalActions target={actionsTarget}><button type="button" onClick={() => onDone(state.result)} className={groupPrimary}>Done</button></ProposalActions>}
    </div></>;
  }

  return <>{sessionNotice}<div hidden={!ready} inert={!ready} className={ready ? "flex flex-col gap-4" : "hidden"}>
    {state.kind === "unknown" && <section className="flex flex-col gap-3 rounded-md border border-border bg-status-caution-bg p-3 text-sm text-ink" aria-label="Unconfirmed group assignment">
      <h3 ref={heading} tabIndex={-1} className="font-bold">Assignment result unconfirmed</h3>
      <p>{assignment.unknownMessage}</p>
      <button type="button" disabled={state.checking} onClick={() => { void assignment.checkSavedAssignment(); }} className="w-fit font-bold underline">{state.checking ? "Checking saved assignment." : "Check saved assignment"}</button>
      {state.readError && <p role="alert">{state.readError}</p>}
      {state.current && <><h4 className="font-bold">Saved assignment now</h4>{state.current.group ? <SavedGroupFacts group={state.current.group} /> : <p>No saved group assignment is recorded.</p>}
        <p>This read shows the current saved record. It does not confirm which request saved it.</p>
        <button type="button" disabled={state.checking} className={groupSecondary} onClick={() => {
          const saved = assignment.continueAfterReadback();
          if (saved) { setBaseline(saved); setIntent(null); }
        }}>Review another assignment</button></>}
      {ready && <ProposalActions target={actionsTarget}><button type="button" disabled={state.checking} onClick={() => onDone(null)} className={groupSecondary}>Close form</button></ProposalActions>}
    </section>}
    <div hidden={state.kind === "unknown"} inert={state.kind === "unknown"} className={state.kind === "unknown" ? "hidden" : "flex flex-col gap-4"}>
      {baseline ? <p className="text-sm text-ink-secondary">Current assignment: {baseline.group ? <><b className="break-words">{baseline.group.name}</b> · Group #{baseline.group.id}</> : "No group assigned"}.</p>
        : <p role="status" className="text-sm text-ink-secondary">Loading the saved assignment.</p>}
      {(current.isError || readError) && <div role="alert" className="flex flex-col gap-2 text-sm text-danger"><p>{readError ?? "Could not load the saved assignment."}</p><button type="button" disabled={refreshing} className="w-fit font-bold underline" onClick={() => { void refreshCurrent(); }}>Try loading saved assignment again</button></div>}
      {state.kind === "rejected" && <div className="flex flex-col gap-2 text-sm text-danger" role="alert"><h3 ref={heading} tabIndex={-1} className="font-bold">Assignment was not saved</h3><p>{state.message}</p>{state.recovery === "current_assignment" && <button type="button" disabled={refreshing} className="w-fit font-bold underline" onClick={() => { void refreshCurrent(); }}>Check current assignment</button>}</div>}
      <fieldset disabled={locked} className="flex flex-col gap-2">
        <legend className="mb-2 text-base font-bold text-ink">Assign manuscript to a group</legend>
        <label className="flex min-h-11 items-center gap-3 text-sm text-ink"><input type="radio" name={intentName} checked={intent === "saved"} onChange={() => setIntent("saved")} />Use a saved group</label>
        <label className="flex min-h-11 items-center gap-3 text-sm text-ink"><input type="radio" name={intentName} checked={intent === "proposal"} onChange={() => setIntent("proposal")} />Review or enter group details</label>
      </fieldset>
      <SavedGroupFields active={ready && intent === "saved" && state.kind !== "unknown"} locked={locked} needsReview={state.kind === "rejected" && state.recovery !== null} onReviewed={() => assignment.markRecoveryReviewed("saved_details")} actionsTarget={actionsTarget} cancelLabel={cancelLabel} onCancel={() => onDone(null)} onManual={() => setIntent("proposal")} onAssign={assignSaved} />
      {intent === "proposal" && initialProposal === undefined && <div className="flex flex-col gap-2 text-sm">
        <button type="button" disabled={locked || proposal.isFetching} onClick={() => {
          setSourceRequested(true);
          if (source) setUseSource(true);
          else if (proposal.isError) void proposal.refetch();
        }} className="w-fit font-bold text-link underline">Review title-page proposal</button>
        {sourceRequested && proposal.isFetching && <p role="status">Loading title-page group details. You can still enter the details yourself.</p>}
        {sourceRequested && proposal.isError && <p role="alert">Could not load the title-page proposal. Enter group details here, or use a saved group.</p>}
        {useSource && <button type="button" disabled={locked} onClick={() => setUseSource(false)} className="w-fit font-bold text-link underline">Return to entered details</button>}
      </div>}
      {baseline && <>
        {initialProposal === undefined && <GroupProposalFields manuscriptId={manuscriptId} proposal={EMPTY} currentGroup={changing ? baseline.group ?? undefined : undefined} manualEntry expectedGroupId={baseline.group?.id ?? null} confirm={confirm} cancelLabel={cancelLabel} active={ready && intent === "proposal" && !useSource && state.kind !== "unknown"} onUseSaved={() => setIntent("saved")} onDone={onDone} actionsTarget={actionsTarget} />}
        {source && <GroupProposalFields manuscriptId={manuscriptId} proposal={source} manualEntry={!hasSourceFields(source)} expectedGroupId={baseline.group?.id ?? null} confirm={confirm} cancelLabel={cancelLabel} active={ready && intent === "proposal" && useSource && state.kind !== "unknown"} onUseSaved={() => setIntent("saved")} onDone={onDone} actionsTarget={actionsTarget} />}
      </>}
      {ready && intent === null && state.kind !== "unknown" && <ProposalActions target={actionsTarget}><button type="button" disabled={working} onClick={() => onDone(null)} className={groupSecondary}>{cancelLabel}</button></ProposalActions>}
    </div>
  </div></>;
}
