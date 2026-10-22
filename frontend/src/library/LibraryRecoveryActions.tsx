// V-081, screen 4z: async Library updates cannot remove an owned recovery action.
import { useEffect, useRef, useState, type FocusEvent, type MouseEvent, type RefObject } from "react";
import { Button } from "../ui/Button";

interface FocusedAction {
  scope: string;
  page?: number;
}

interface Props {
  focusScope: RefObject<HTMLElement | null>;
  scope: string;
  goPage?: number;
  retryAvailable: boolean;
  onGo: (event: MouseEvent<HTMLButtonElement>, page: number) => void;
  onRetry: (event: MouseEvent<HTMLButtonElement>) => void;
}

export function LibraryRecoveryActions({ focusScope, scope, goPage, retryAvailable, onGo, onRetry }: Props) {
  const [focusedGo, setFocusedGo] = useState<FocusedAction>();
  const [focusedRetry, setFocusedRetry] = useState<FocusedAction>();
  const goRef = useRef<HTMLButtonElement>(null);
  const retryRef = useRef<HTMLButtonElement>(null);
  const target = focusedGo?.page ?? goPage;
  const goAvailable = target !== undefined && goPage === target
    && (!focusedGo || focusedGo.scope === scope);
  const retryEnabled = retryAvailable && (!focusedRetry || focusedRetry.scope === scope);

  useEffect(() => {
    if (!focusedGo && !focusedRetry) return;
    const root = focusScope.current;
    // A neutral blur can leave BODY active. A later in-page transfer then
    // emits no second blur on the old button, so listen within this route.
    const retireOtherAction = (event: Event) => {
      const next = event.target;
      if (!(next instanceof Element) || !next.isConnected) return;
      if (next !== goRef.current) setFocusedGo(undefined);
      if (next !== retryRef.current) setFocusedRetry(undefined);
    };
    root?.addEventListener("focusin", retireOtherAction);
    return () => root?.removeEventListener("focusin", retireOtherAction);
  }, [focusScope, focusedGo, focusedRetry]);

  function hasInPageDestination(event: FocusEvent<HTMLButtonElement>) {
    const next = event.relatedTarget;
    return next instanceof HTMLElement && next.isConnected
      && next !== document.body && next !== document.documentElement;
  }

  if (target === undefined && !retryAvailable && !focusedRetry) return null;
  return (
    <div className="signal-library-state">
      {target !== undefined && (
        <Button
          ref={goRef}
          type="button"
          variant="secondary"
          aria-disabled={goAvailable ? undefined : true}
          onFocus={() => setFocusedGo((current) => current ?? { scope, page: target })}
          onBlur={(event) => { if (hasInPageDestination(event)) setFocusedGo(undefined); }}
          onClick={(event) => { if (goAvailable) onGo(event, target); }}
        >
          Go to page {target}
        </Button>
      )}
      {(retryAvailable || focusedRetry) && (
        <Button
          ref={retryRef}
          type="button"
          variant="secondary"
          aria-disabled={retryEnabled ? undefined : true}
          onFocus={() => setFocusedRetry((current) => current ?? { scope })}
          onBlur={(event) => { if (hasInPageDestination(event)) setFocusedRetry(undefined); }}
          onClick={(event) => { if (retryEnabled) onRetry(event); }}
        >
          Try again
        </Button>
      )}
    </div>
  );
}

