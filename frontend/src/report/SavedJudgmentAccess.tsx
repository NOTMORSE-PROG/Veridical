import { hasPendingRouteReturnFocus } from "../routing/useRouteFocus";
// F8, screens 4h/4w: acknowledgment and retrieval stay distinct from grading.
import { useEffect, useRef, useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import type { ResultRowCommon } from "../api/types";
import { CRITERION_EXCLUSION_COPY } from "../domain/criterionReview";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { Button } from "../ui/Button";
import { useCriterionReviewScope, type ReviewCompletion } from "./criterionReviewContext";
import { criterionRecordId, recordedGradeLabel, revealCriterionRecord, atLeastSavedJudgment, SAVED_CRITERION_PARAM } from "./savedJudgment";
import { useReport } from "./useReport";
import { judgmentVersion } from "./judgmentRecord";

export function SavedJudgmentAccess({ checkRunId, hideEmpty = false }: { checkRunId: number; hideEmpty?: boolean }) {
  const review = useCriterionReviewScope(checkRunId);
  const query = useReport(checkRunId);
  const [focusedReceipt, setFocusedReceipt] = useState<ReviewCompletion>();
  const retainedReceipt = focusedReceipt ?? review.completion;
  const receipt = retainedReceipt && !review.revisions.records.get(retainedReceipt.result.criterion_id)?.unavailable ? retainedReceipt : undefined;
  const results = Array.isArray(query.data?.results) ? query.data.results : undefined;
  const count = results?.filter((row) => row.resolution != null).length;
  const refreshing = query.fetchStatus !== "idle";
  const awaitingConfirmed = [...review.confirmed.values()].some((saved) => !results?.some((row) => atLeastSavedJudgment(row, saved)));
  const qualified = refreshing || query.isError || awaitingConfirmed;
  const currentReceiptRow = results?.find(row => row.criterion_id === receipt?.result.criterion_id);
  const pastReceipt = receipt && currentReceiptRow && judgmentVersion(receipt.result) !== undefined
    && judgmentVersion(currentReceiptRow) !== undefined && judgmentVersion(currentReceiptRow)! > judgmentVersion(receipt.result)!;
  useEffect(() => {
    const completion = review.completion;
    if (completion) completion.focus?.focus(document.getElementById(`saved-receipt-${checkRunId}-${completion.result.criterion_id}`));
  }, [review.completion, checkRunId]);
  return <div className="signal-saved-judgment-access signal-group-flow">
    {receipt && <div key={receipt.result.criterion_id} className="signal-attached-flow"
      onFocusCapture={() => setFocusedReceipt(receipt)}
      onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocusedReceipt(undefined); }}>
      <h3 id={`saved-receipt-${checkRunId}-${receipt.result.criterion_id}`} tabIndex={-1}>Judgment saved</h3>
      {pastReceipt && <p>Past saved acknowledgment. View the recorded judgment for its current state.</p>}
      <p>{receipt.result.text}</p>
      {receipt.result.resolution?.type === "needs_document"
        ? <p>{CRITERION_EXCLUSION_COPY.recorded}. {CRITERION_EXCLUSION_COPY.noGrade}</p>
        : <p><strong>Recorded grade:</strong> {recordedGradeLabel(receipt.result)}</p>}
      <Button variant="secondary" aria-label={`View saved judgment: ${receipt.result.text}`}
        onClick={(event) => review.viewSaved(receipt.result.criterion_id, event.currentTarget)}>View saved judgment</Button>
    </div>}
    {count !== undefined && <div className="signal-control-cluster">
      {qualified && <p className="signal-field-hint">Last loaded: {count} saved instructor {count === 1 ? "judgment" : "judgments"}.</p>}
      {count > 0
        ? <Button variant="quiet" onClick={(event) => review.viewSaved(undefined, event.currentTarget)}>{qualified ? "View last loaded saved judgments" : `View saved judgments (${count})`}</Button>
        : !qualified && !hideEmpty && <p className="signal-field-hint">No saved instructor judgments yet.</p>}
    </div>}
    {refreshing && <p className="signal-field-hint" role="status">{query.fetchStatus === "paused" ? "Waiting to update saved judgments." : "Updating saved judgments."}</p>}
    {query.isError && <p className="signal-field-hint">Could not load the current saved judgments. <Button variant="quiet" onClick={() => { void query.refetch(); }}>Retry saved judgments</Button></p>}
    {!refreshing && !query.isError && awaitingConfirmed && <p className="signal-field-hint">Your judgment was saved, but the loaded report does not confirm every saved result. <Button variant="quiet" onClick={() => { void query.refetch(); }}>Retry saved judgments</Button></p>}
  </div>;
}

export function SavedCriterionDestination({ checkRunId, results }: { checkRunId: number; results: ResultRowCommon[] }) {
  const [params] = useSearchParams();
  const location = useLocation();
  const review = useCriterionReviewScope(checkRunId);
  const query = useReport(checkRunId);
  const raw = params.get(SAVED_CRITERION_PARAM);
  const parsed = Number(raw);
  const criterionId = raw !== null && raw !== "all" && Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
  const all = raw === "all";
  const confirmed = criterionId === undefined ? undefined : review.confirmed.get(criterionId);
  const available = all || (criterionId !== undefined && !review.revisions.records.get(criterionId)?.unavailable
    && results.some((row) => row.criterion_id === criterionId && (!confirmed || atLeastSavedJudgment(row, confirmed))));
  const navigation = review.savedNavigation;
  const ownNavigation = navigation && (all ? navigation.criterionId === undefined : navigation.criterionId === criterionId);
  const claim = useRef<FocusContinuation | undefined>(undefined);
  useEffect(() => {
    if (raw === null) return;
    const owner = ownNavigation ? navigation.owner : document.activeElement;
    const mayOwn = ownNavigation ? owner !== null && document.activeElement === owner
      : !hasPendingRouteReturnFocus(location.pathname + location.search) && (owner === document.body || owner?.matches("h1"));
    if (mayOwn && document.hasFocus()) claim.current = claimFocusContinuation(owner);
    return () => { claim.current?.dispose(); claim.current = undefined; };
  }, [raw, checkRunId, ownNavigation, navigation?.version, navigation?.owner, location.pathname, location.search, location.hash]);
  useEffect(() => {
    if (raw === null || !available) return;
    const target = document.getElementById(all ? "criteria-results-heading" : criterionRecordId(checkRunId, criterionId!));
    if (!target || target.closest("[hidden]") || !claim.current?.owns()) return;
    revealCriterionRecord(target);
    if (claim.current?.focus(target)) target.scrollIntoView({ block: "nearest" });
  }, [raw, all, available, criterionId, checkRunId, query.data, navigation?.version, location.hash]);
  if (raw === null || available) return null;
  const waiting = query.isPending || query.fetchStatus !== "idle";
  return <div className="signal-attached-flow" role="status">
    <p>{waiting ? "Loading the recorded judgment. Your saved acknowledgment remains above."
      : confirmed ? "Your judgment was saved, but the recorded result could not be loaded."
        : query.isError ? "The recorded criterion could not be loaded." : "That recorded criterion is not available in this report."}</p>
    {!waiting && (query.isError || confirmed) && <Button variant="secondary" onClick={() => { void query.refetch(); }}>Retry recorded result</Button>}
  </div>;
}
