import { useLayoutEffect, useRef } from "react";
import { claimFocusContinuation, type FocusContinuation } from "./focusContinuation";

/** F8, screens 4h/4i/4w: continue an explicit action after its control disappears. */
export function useExplicitActionFocus(target: () => HTMLElement | null) {
  const continuation = useRef<{ claim: FocusContinuation; trigger: HTMLElement } | undefined>(undefined);
  useLayoutEffect(() => {
    const pending = continuation.current;
    if (pending) {
      if (!pending.trigger.isConnected) pending.claim.focus(target());
      else pending.claim.dispose();
    }
    continuation.current = undefined;
  });
  useLayoutEffect(() => () => continuation.current?.claim.dispose(), []);
  return (trigger: HTMLElement, action: () => void) => {
    continuation.current?.claim.dispose();
    continuation.current = document.activeElement === trigger
      ? { claim: claimFocusContinuation(trigger), trigger } : undefined;
    action();
  };
}
