// F3/F8, screens 4h/4w: private drafts live only in this report route family.
import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { Outlet, useParams } from "react-router";
import { ME_QUERY_KEY, useMe } from "../auth/useAuth";
import { RESOLUTION_REASON_MIN_LENGTH } from "../config/ui";
import { useResolveEscalation } from "./useReport";
import { ReviewContext, type ReviewCompletion, type ReviewDraft, type ReviewScope } from "./criterionReviewContext";

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
  const [authQuery] = useState(() => client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }));
  const alive = useRef(false);
  const records = useRef(new Map<number, ReviewDraft>());
  const [drafts, setDrafts] = useState(records.current);
  const [completion, setCompletion] = useState<ReviewCompletion>();
  useLayoutEffect(() => {
    alive.current = true;
    return () => { alive.current = false; records.current.clear(); };
  }, []);
  function isCurrent() {
    return alive.current && authQuery !== undefined
      && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === authQuery
      && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === instructorId;
  }
  const resolve = useResolveEscalation(checkRunId, isCurrent);
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
    checkRunId, drafts, completion,
    choose(item, resolution, level) {
      if (!isCurrent() || records.current.get(item.check_result_id)?.saving) return;
      publish(new Map(records.current).set(item.check_result_id, {
        criterionId: item.criterion_id, resolution, level, reason: "", attempted: false, saving: false,
      }));
    },
    changeReason(id, reason) { update(id, (draft) => ({ ...draft, reason, attempted: false, error: undefined })); },
    cancel(id) {
      if (!isCurrent() || records.current.get(id)?.saving) return;
      const next = new Map(records.current); next.delete(id); publish(next);
    },
    confirm(id, message) {
      const draft = records.current.get(id);
      if (!isCurrent() || !draft || draft.saving) return;
      if (draft.reason.trim().length < RESOLUTION_REASON_MIN_LENGTH) {
        update(id, (current) => ({ ...current, attempted: true }));
        return;
      }
      const submitted = { ...draft, attempted: true, saving: true, error: undefined };
      publish(new Map(records.current).set(id, submitted));
      const focusFrom = document.activeElement;
      // The route owner, not an unmounted card's mutate callback, consumes
      // completion. No private draft is copied into report query records.
      void resolve.mutateAsync({ checkResultId: id, criterionId: draft.criterionId, resolution: draft.resolution, level: draft.level, reason: draft.reason.trim() }).then((out) => {
        if (!isCurrent() || records.current.get(id) !== submitted) return;
        const completionMessage = message(out);
        const next = new Map(records.current); next.delete(id); publish(next);
        setCompletion((previous) => ({ version: (previous?.version ?? 0) + 1, message: completionMessage,
          focusFrom: focusFrom?.isConnected && document.activeElement === focusFrom ? focusFrom : null }));
      }).catch((error: unknown) => {
        if (!isCurrent() || records.current.get(id) !== submitted) return;
        publish(new Map(records.current).set(id, { ...submitted, saving: false, error }));
      });
    },
    reconcile(items) {
      if (!isCurrent()) return;
      const unresolved = new Set(items.map((item) => item.check_result_id));
      const next = new Map([...records.current].filter(([id, draft]) => draft.saving || unresolved.has(id)));
      if (next.size !== records.current.size) publish(next);
    },
  };
  return <ReviewContext.Provider value={scope}>{children}</ReviewContext.Provider>;
}
