import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { useAuthOwner } from "../auth/queryOwner";
import { claimFocusContinuation, type FocusContinuation } from "../routing/focusContinuation";
import { useExportReportPdf } from "./useReport";

type Request = ReturnType<ReturnType<typeof useExportReportPdf>["createRequest"]>;
type Attempt = { request: Request; pending: boolean; focus: FocusContinuation | null };
type ActionState = { attempt: Attempt; status: "pending" | "ready" | "error"; error?: unknown };

/** F8.6, screen 4h: delivery may outlive a page; its status and focus may not. */
export function useReportPdfExportAction(checkRunId: number, errorTarget: { current: HTMLDivElement | null }) {
  const owner = useAuthOwner();
  const scope = useMemo(() => ({ checkRunId, owner, live: false, attempt: null as Attempt | null }), [checkRunId, owner]);
  const [state, setState] = useState<ActionState | null>(null);
  const exportPdf = useExportReportPdf(checkRunId);
  const ownsScope = useCallback(() => scope.live && scope.owner.isCurrent(), [scope]);
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
