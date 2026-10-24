// F3/F8, screens 4h/4w: private drafts live only in this report route family.
import { useIsMutating, useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { Outlet, useBlocker, useLocation, useNavigate, useParams } from "react-router";
import { reportAssessmentNotices } from "../domain/reportAssessmentContext";
import { ApiError } from "../api/client";
import { Dialog } from "../ui/Dialog";
import { Button } from "../ui/Button";
import type { ResultRowCommon } from "../api/types";
import { ME_QUERY_KEY, useMe } from "../auth/useAuth";
import { RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";
import { useEscalatedItems, useReport, useResolveEscalation } from "./useReport";
import { isAssessmentRevision } from "./assessmentRevision";
import { criterionReviewNotices } from "../domain/criterionReview";
import { ReviewContext, type ReviewCompletion, type ReviewDraft, type ReviewScope } from "./criterionReviewContext";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { confirmedJudgment, criterionRecordId, revealCriterionRecord, SAVED_CRITERION_PARAM } from "./savedJudgment";
import { useJudgmentRevisions } from "./useJudgmentRevisions";
import type { RevisionDraft } from "./useJudgmentRevisions";

// Query identity changes on cache clear, including logout/login to the same
// instructor. Data identity alone does not distinguish those auth lifetimes.
function useAuthQueryIdentity() {
  const client = useQueryClient();
  return useSyncExternalStore(
    (notify) => client.getQueryCache().subscribe((event) => {
      if ((event.type === "added" || event.type === "removed")
        && event.query.queryKey.length === ME_QUERY_KEY.length
        && event.query.queryKey.every((part: unknown, index: number) => part === ME_QUERY_KEY[index])) notify();
    }),
    () => client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }),
  );
}

export function CriterionReviewLayout() {
  const { checkRunId: rawId } = useParams();
  const { data: instructor } = useMe();
  const authQuery = useAuthQueryIdentity();
  const [identity, setIdentity] = useState({ query: authQuery, version: 0 });
  if (identity.query !== authQuery) {
    setIdentity({ query: authQuery, version: identity.version + 1 });
    return null;
  }
  if (!instructor) return null;
  return <CriterionReviewProvider key={`${instructor.id}:${rawId}:${identity.version}`} checkRunId={Number(rawId)} instructorId={instructor.id}><Outlet /></CriterionReviewProvider>;
}

export function CriterionReviewProvider({ checkRunId, instructorId, children }: {
  checkRunId: number; instructorId: number; children: ReactNode;
}) {
  const client = useQueryClient();
  const location = useLocation();
  const mutating = useIsMutating();
  const navigate = useNavigate();
  const [authQuery] = useState(() => client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }));
  const alive = useRef(false);
  const records = useRef(new Map<number, ReviewDraft>());
  const [drafts, setDrafts] = useState(records.current);
  const [completion, setCompletion] = useState<ReviewCompletion>();
  const confirmedRecords = useRef(new Map<number, ResultRowCommon>());
  const [confirmed, setConfirmed] = useState<ReadonlyMap<number, ResultRowCommon>>(confirmedRecords.current);
  const [savedNavigation, setSavedNavigation] = useState<ReviewScope["savedNavigation"]>();
  const continuations = useRef(new Set<FocusContinuation>());
  useLayoutEffect(() => {
    alive.current = true;
    const confirmedForLifetime = confirmedRecords.current;
    const continuationsForLifetime = continuations.current;
    return () => { alive.current = false; records.current.clear(); confirmedForLifetime.clear(); continuationsForLifetime.forEach((claim) => claim.dispose()); };
  }, []);
  useLayoutEffect(() => { continuations.current.forEach((claim) => claim.dispose()); continuations.current.clear(); }, [location.key]);
  function isCurrent() {
    return alive.current && authQuery !== undefined
      && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === authQuery
      && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === instructorId;
  }
  const exitKind = useRef<"drafts" | "saving" | "uncertain" | "revisionUnknown">("drafts");
  const exitCapture = useRef<{ pending: Map<number, ReviewDraft>; revisions: ReadonlyMap<number, RevisionDraft> } | undefined>(undefined);
  const reportQuery = useReport(checkRunId);
  const assessment = reportQuery.isSuccess ? reportQuery.data : undefined;
  const revisions = useJudgmentRevisions(checkRunId, isCurrent, assessment,
    reportQuery.isSuccess && reportQuery.fetchStatus === "idle", () => reportQuery.refetch(),
    () => mutating > 0 || client.getMutationCache().getAll().some(mutation => mutation.state.status === "pending")
      || [...records.current.values()].some(draft => draft.saving));
  useLayoutEffect(() => {
    function beforeUnload(event: BeforeUnloadEvent) {
      if (!isCurrent() || (records.current.size === 0 && !revisions.hasDrafts)) return;
      event.preventDefault(); event.returnValue = "";
    }
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  });
  const blocker = useBlocker(({ nextLocation }) => {
    const sameOwner = nextLocation.pathname === `/report/${checkRunId}`
      || nextLocation.pathname === `/report/${checkRunId}/document`;
    if (sameOwner || nextLocation.pathname === "/signin" || !isCurrent()
      || authQuery?.state.status !== "success" || (records.current.size === 0 && !revisions.hasDrafts)) return false;
    exitKind.current = revisions.busy || [...records.current.values()].some(draft => draft.saving) ? "saving"
      : [...records.current.values()].some(draft => draft.uncertainRevision !== undefined) ? "uncertain"
        : revisions.uncertain ? "revisionUnknown" : "drafts";
    exitCapture.current = { pending: new Map(records.current), revisions: revisions.captureDrafts() };
    return true;
  });
  const resolve = useResolveEscalation(checkRunId, isCurrent);
  const itemsQuery = useEscalatedItems(checkRunId);
  const canChoose = assessment?.decision === null && isAssessmentRevision(assessment.assessment_revision);
  useLayoutEffect(() => {
    if (!assessment?.decision) return;
    const next = new Map(records.current);
    let changed = false;
    next.forEach((draft, id) => {
      if (!draft.frozen && !draft.saving) { next.set(id, { ...draft, frozen: true }); changed = true; }
    });
    if (changed) publish(next);
  }, [assessment?.decision, assessment?.assessment_revision]);
  function isDraftCurrent(draft: ReviewDraft) {
    return canChoose && !draft.frozen && !draft.needsChoice && !draft.currentResolved
      && draft.expectedRevision === assessment?.assessment_revision;
  }
  function publish(next: Map<number, ReviewDraft>) {
    records.current = next;
    setDrafts(next);
  }
  function update(id: number, change: (draft: ReviewDraft) => ReviewDraft) {
    const draft = records.current.get(id);
    if (!isCurrent() || !draft || draft.saving) return;
    publish(new Map(records.current).set(id, change(draft)));
  }
  const scope: ReviewScope = {
    checkRunId, decision: reportQuery.data?.decision, drafts, completion, confirmed, savedNavigation, isDraftCurrent, canChoose, revisions,
    assessmentNotices: reportAssessmentNotices(reportQuery.data, location.pathname.endsWith("/document") ? "manuscript" : "report"),
    canUseCurrentDraft: draft => canChoose && !draft.currentResolved
      && isAssessmentRevision(draft.reviewedRevision) && draft.reviewedRevision === assessment?.assessment_revision,
    viewSaved(criterionId, trigger) {
      if (!isCurrent()) return;
      const target = document.getElementById("criteria-results-heading");
      const owned = document.hasFocus() && document.activeElement === trigger;
      if (target && owned) { revealCriterionRecord(target); target.focus(); }
      setSavedNavigation((previous) => ({ version: (previous?.version ?? 0) + 1, criterionId,
        owner: target && owned && document.activeElement === target ? target : null }));
      const next = new URLSearchParams(location.search);
      next.set(SAVED_CRITERION_PARAM, criterionId === undefined ? "all" : String(criterionId));
      if (location.pathname.endsWith("/document")) { next.set("view", "decision"); next.set("pane", "review"); }
      void navigate({ pathname: location.pathname, search: next.toString(),
        hash: criterionId === undefined ? "criteria-results-heading" : criterionRecordId(checkRunId, criterionId) }, { state: location.state });
    },
    choose(item, resolution, level) {
      const previous = records.current.get(item.check_result_id);
      if (!isCurrent() || !canChoose || !isAssessmentRevision(assessment?.assessment_revision)
        || confirmedRecords.current.has(item.criterion_id) || previous?.saving
        || (previous && !previous.needsChoice && !isDraftCurrent(previous))) return;
      publish(new Map(records.current).set(item.check_result_id, {
        criterionId: item.criterion_id, criterionText: item.criterion_text,
        expectedRevision: assessment.assessment_revision, resolution, level,
        reason: previous?.needsChoice ? previous.reason : "", attempted: false, saving: false,
      }));
    },
    changeReason(id, reason) {
      const draft = records.current.get(id);
      if (draft && isDraftCurrent(draft)) update(id, (draft) => ({ ...draft, reason, attempted: false, error: undefined }));
    },
    cancel(id) {
      if (!isCurrent() || records.current.get(id)?.saving || records.current.get(id)?.uncertainRevision !== undefined) return;
      const next = new Map(records.current); next.delete(id); publish(next);
    },
    confirm(id, message) {
      const draft = records.current.get(id);
      if (!isCurrent() || !draft || draft.saving || !isDraftCurrent(draft)) return;
      if (draft.reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) {
        update(id, (current) => ({ ...current, attempted: true }));
        return;
      }
      const submitted = { ...draft, attempted: true, saving: true, error: undefined };
      const focusFrom = document.activeElement;
      let focus: FocusContinuation | undefined;
      if (document.hasFocus() && focusFrom?.matches("[data-criterion-confirm]")) {
        const continuation = document.getElementById("escalated-heading");
        continuation?.focus();
        if (continuation && document.activeElement === continuation) {
          focus = claimFocusContinuation(continuation);
          continuations.current.add(focus);
        }
      }
      publish(new Map(records.current).set(id, submitted));
      // The route owner, not an unmounted card's mutate callback, consumes
      // completion. No private draft is copied into report query records.
      void resolve.mutateAsync({ checkResultId: id, criterionId: draft.criterionId, resolution: draft.resolution, level: draft.level, reason: draft.reason.trim(), expectedRevision: draft.expectedRevision }).then((out) => {
        if (!isCurrent() || records.current.get(id) !== submitted) return;
        const result = confirmedJudgment(out, draft.criterionId, { resolution: draft.resolution, reason: draft.reason.trim(), level: draft.level });
        if (!result) return;
        const completionMessage = message(out);
        confirmedRecords.current = new Map(confirmedRecords.current).set(result.criterion_id, result);
        setConfirmed(confirmedRecords.current);
        const next = new Map(records.current); next.delete(id); publish(next);
        setCompletion((previous) => {
          previous?.focus?.dispose();
          return { version: (previous?.version ?? 0) + 1, message: completionMessage, result, focus,
            focusFrom: focusFrom?.isConnected && document.activeElement === focusFrom ? focusFrom : null };
        });
      }).catch((error: unknown) => {
        focus?.dispose();
        if (!isCurrent() || records.current.get(id) !== submitted) return;
        // A genuine refusal is a no-write result under the transaction contract.
        // Preserve any earlier ambiguous submission until its revision is fenced.
        const refused = error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408;
        publish(new Map(records.current).set(id, { ...submitted, saving: false, frozen: true, reviewedRevision: undefined, error,
          uncertainRevision: refused ? submitted.uncertainRevision : submitted.expectedRevision }));
      });
    },
    reconcile(items) {
      if (!isCurrent()) return;
      const unresolved = new Set(items.map((item) => item.check_result_id));
      const next = new Map(records.current);
      let changed = false;
      next.forEach((draft, id) => {
        if (!draft.saving && !unresolved.has(id) && !draft.currentResolved) {
          next.set(id, { ...draft, currentResolved: true, frozen: true }); changed = true;
        }
      });
      if (changed) publish(next);
    },
    reviewCurrent(id) {
      const draft = records.current.get(id);
      if (!isCurrent() || !draft || draft.saving) return;
      void Promise.all([reportQuery.refetch(), itemsQuery.refetch()]).then(([reportRead, itemRead]) => {
        if (!isCurrent() || records.current.get(id) !== draft) return;
        const current = reportRead.data;
        if (!reportRead.isSuccess || !itemRead.isSuccess || !isAssessmentRevision(current?.assessment_revision)) {
          update(id, d => ({ ...d, error: new Error("The current criterion could not be loaded. Your draft is kept; retry the current review.") }));
          return;
        }
        update(id, d => ({ ...d, reviewedRevision: current.assessment_revision!,
          uncertainRevision: d.uncertainRevision !== undefined && current.assessment_revision! <= d.uncertainRevision ? d.uncertainRevision : undefined,
          currentResolved: !itemRead.data.some(item => item.check_result_id === id && item.criterion_id === d.criterionId), error: undefined }));
      }).catch(() => {
        if (isCurrent() && records.current.get(id) === draft) update(id, d => ({ ...d,
          error: new Error("The current criterion could not be loaded. Your draft is kept; retry the current review.") }));
      });
    },
    useCurrentDraft(id) {
      const draft = records.current.get(id);
      if (!isCurrent() || !draft || draft.saving || !canChoose || draft.currentResolved
        || draft.reviewedRevision !== assessment?.assessment_revision || !isAssessmentRevision(draft.reviewedRevision)) return;
      const item = itemsQuery.isSuccess ? itemsQuery.data.find(item => item.check_result_id === id && item.criterion_id === draft.criterionId) : undefined;
      if (!item) return;
      const supported = draft.resolution === "needs_document"
        || (draft.resolution === "mark_level" ? item.levels?.some(level => level.level === draft.level)
          : draft.resolution === "accept_majority" ? item.ai_majority_verdict !== null && !item.verdict_unrecognized
            && !criterionReviewNotices(item).some(notice => notice.kind === "support")
            : !item.levels?.length);
      update(id, d => ({ ...d, expectedRevision: draft.reviewedRevision!, frozen: false,
        reviewedRevision: undefined, needsChoice: !supported, attempted: false, error: undefined }));
    },
  };
  return <ReviewContext.Provider value={scope}>{children}
    {blocker.state === "blocked" && <Dialog
      title={exitKind.current === "drafts" ? "Leave this review with unsaved changes?"
        : exitKind.current === "revisionUnknown" ? "Leave with an unconfirmed revision save?" : "Stay in this review"}
      initialFocusTitle onClose={() => blocker.reset()}
      actions={<>
        <Button variant="secondary" onClick={() => blocker.reset()}>Keep reviewing</Button>
        {exitKind.current === "revisionUnknown" && <Button variant="danger" disabled={!revisions.canStop()} onClick={() => {
          const captured = exitCapture.current;
          if (!isCurrent() || !captured || !revisions.canStop()
            || [...records.current.values()].some(draft => draft.saving || draft.uncertainRevision !== undefined)
            || captured.pending.size !== records.current.size
            || [...records.current].some(([id, draft]) => captured.pending.get(id) !== draft)
            || !revisions.abandonAll(captured.revisions)) { blocker.reset(); return; }
          publish(new Map()); blocker.proceed();
        }}>Stop editing and leave</Button>}
        {exitKind.current === "drafts" && <Button variant="danger" onClick={() => {
          // Recheck the live owner: a sibling save may have begun since the intent.
          const captured = exitCapture.current;
          if (!isCurrent() || !captured || !revisions.canStop() || revisions.uncertain
            || [...records.current.values()].some(draft => draft.saving || draft.uncertainRevision !== undefined)
            || captured.pending.size !== records.current.size
            || [...records.current].some(([id, draft]) => captured.pending.get(id) !== draft)
            || !revisions.abandonAll(captured.revisions)) {
            blocker.reset(); return;
          }
          publish(new Map()); blocker.proceed();
        }}>Leave without saving</Button>}
      </>}>
      <p>{exitKind.current === "drafts"
        ? "Your unsaved criterion choices and reasons will be discarded if you leave this review."
        : exitKind.current === "revisionUnknown"
          ? "The outcome of an earlier revision save is unknown. Leaving removes your entered revision from this review. It does not cancel or reverse the earlier request. Review the current judgment when you return."
        : revisions.busy || [...drafts.values()].some(draft => draft.saving)
          ? "A criterion judgment is being saved. Stay in this review until saving is confirmed."
          : revisions.uncertain || [...drafts.values()].some(draft => draft.uncertainRevision !== undefined)
            ? "Saving could not be confirmed. Your choice and reason are kept. Select Keep reviewing, then review the current criterion before trying again."
            : "The review status has changed. Select Keep reviewing, then choose your destination again."}</p>
      {exitKind.current === "revisionUnknown" && (drafts.size > 0 || [...revisions.records.values()].some(record => record.draft && record.draft.uncertainRevision === undefined))
        && <p>Your other unsaved criterion choices and reasons will also be discarded.</p>}
    </Dialog>}
  </ReviewContext.Provider>;
}
