import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useState, useSyncExternalStore } from "react";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { useExportReportPdf } from "./useReport";

type Request = ReturnType<ReturnType<typeof useExportReportPdf>["createRequest"]>;
type Attempt = { request: Request; pending: boolean; focus: FocusContinuation | null };
type ActionState = { attempt: Attempt; status: "pending" | "ready" | "error"; error?: unknown };

/** F8.6, screen 4h: delivery may outlive a page; its status and focus may not. */
export function useReportPdfExportAction(checkRunId: number, errorTarget: { current: HTMLDivElement | null }) {
  const queryClient = useQueryClient();
  const cache = queryClient.getQueryCache();
  const authState = useSyncExternalStore(
    listener => cache.subscribe(listener),
    () => cache.find({ queryKey: ME_QUERY_KEY, exact: true })?.state,
  );
  const authQuery = cache.find({ queryKey: ME_QUERY_KEY, exact: true });
  const actorId = (authState?.data as { id?: number } | null)?.id;
  const scope = useMemo(() => ({ checkRunId, authQuery, actorId, live: false, attempt: null as Attempt | null }), [checkRunId, authQuery, actorId]);
  const [state, setState] = useState<ActionState | null>(null);
  const exportPdf = useExportReportPdf(checkRunId);
  const ownsScope = useCallback(() => scope.live && scope.authQuery !== undefined && scope.actorId !== undefined
    && cache.find({ queryKey: ME_QUERY_KEY, exact: true }) === scope.authQuery
    && queryClient.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === scope.actorId, [scope, cache, queryClient]);
  const current = useCallback((attempt: Attempt) => ownsScope() && scope.attempt === attempt && attempt.request.owner.isCurrent(), [scope, ownsScope]);

  useLayoutEffect(() => {
    scope.live = true;
    return () => { scope.live = false; scope.attempt?.focus?.dispose(); };
  }, [scope]);

  useEffect(() => {
    if (state?.status !== "error") return;
    if (current(state.attempt)) state.attempt.focus?.focus(errorTarget.current);
    else state.attempt.focus?.dispose();
    state.attempt.focus = null;
  }, [state, current, errorTarget]);

  function start(trigger: HTMLButtonElement) {
    if (!ownsScope() || scope.attempt?.pending) return;
    scope.attempt?.focus?.dispose();
    const request = exportPdf.createRequest();
    if (!request.owner.isCurrent()) return;
    const attempt: Attempt = { request, pending: true,
      focus: document.activeElement === trigger && document.hasFocus() ? claimFocusContinuation(trigger) : null };
    scope.attempt = attempt;
    setState({ attempt, status: "pending" });
    exportPdf.mutate(request, {
      onSuccess: () => {
        attempt.focus?.dispose(); attempt.focus = null;
        if (current(attempt)) setState({ attempt, status: "ready" });
      },
      onError: error => {
        if (current(attempt)) setState({ attempt, status: "error", error });
        else { attempt.focus?.dispose(); attempt.focus = null; }
      },
      onSettled: () => { if (scope.attempt === attempt) attempt.pending = false; },
    });
  }

  const visible = state && current(state.attempt) ? state : null;
  return { start, isPending: visible?.status === "pending", isError: visible?.status === "error",
    isReady: visible?.status === "ready", error: visible?.error };
}
