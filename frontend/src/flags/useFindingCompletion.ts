import { useEffect, useLayoutEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useActionLifetime } from "../auth/useActionLifetime";

/** A replaced owner cannot regain an old accepted receipt by signing back in. */
export function useFindingCompletionOwner(purpose: string) {
  const client = useQueryClient();
  const isCurrent = useActionLifetime(purpose);
  const retired = useRef(false);
  useLayoutEffect(() => client.getQueryCache().subscribe(() => {
    if (!isCurrent()) retired.current = true;
  }), [client]);
  return () => !retired.current && isCurrent();
}

/** F8, screen 4i: one accepted modal action owns its close continuation. */
export function useFindingCompletion() {
  const pending = useRef<{ dialog: Element | null; allowed: boolean } | null>(null);
  const accepted = useRef(false);
  useEffect(() => {
    const blur = () => { if (pending.current) pending.current.allowed = false; };
    const focus = (event: FocusEvent) => {
      const claim = pending.current;
      const target = event.target;
      // Tab, pointer and wheel within the genuine pending modal retain its
      // ownership. A blocked inert backdrop is not a new workflow destination.
      if (claim && target instanceof HTMLElement && target !== document.body
        && !claim.dialog?.contains(target) && !target.closest("[inert], [hidden], [aria-hidden='true']")) claim.allowed = false;
    };
    window.addEventListener("blur", blur);
    document.addEventListener("focusin", focus, true);
    return () => {
      window.removeEventListener("blur", blur);
      document.removeEventListener("focusin", focus, true);
    };
  }, []);
  return {
    begin: (field: HTMLElement | null) => {
      accepted.current = false;
      pending.current = { dialog: field?.closest("[role='dialog']") ?? null, allowed: document.hasFocus() };
    },
    accept: () => { accepted.current = true; },
    returnTarget: (target: HTMLElement | null) => accepted.current ? target : null,
    shouldRestore: (isCurrent: () => boolean) => !accepted.current || (isCurrent() && pending.current?.allowed === true),
  };
}
