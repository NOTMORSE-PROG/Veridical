// F7/F8, screen 4w: reveal a selected source without taking review focus.
import { useCallback, useLayoutEffect, useRef, type RefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { recordSourceReadingPlacement } from "./useDocxReadingPosition";

export function useSelectedSourceReveal({ runId, search, requestedId, resolvedId, visible, source }: {
  runId: number; search: string; requestedId: number | null; resolvedId: number | null;
  visible: boolean; source: RefObject<HTMLElement | null>;
}) {
  const client = useQueryClient();
  const initial = useRef(true);
  const attempt = useRef<{ runId: number; from: string; destination: string; id: number;
    owner: Element | null; allowed: boolean; auth: unknown; instructorId: number | undefined } | undefined>(undefined);
  const auth = useCallback(() => client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }), [client]);
  function arm(destination: string, id: number, from = search) {
    attempt.current = { runId, from, destination, id, owner: document.activeElement,
      allowed: document.hasFocus() && auth() !== undefined, auth: auth(),
      instructorId: client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id };
  }
  function adopt(owner: HTMLElement | null) {
    const pending = attempt.current;
    if (pending?.allowed && document.activeElement === pending.owner) pending.owner = owner;
  }
  useLayoutEffect(() => {
    const retire = () => { if (attempt.current) attempt.current.allowed = false; };
    const focus = (event: FocusEvent) => { if (event.target !== attempt.current?.owner) retire(); };
    const key = (event: KeyboardEvent) => {
      if (["Tab", "PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End", " "].includes(event.key)) retire();
    };
    document.addEventListener("focusin", focus, true);
    document.addEventListener("pointerdown", retire, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("wheel", retire, { passive: true });
    window.addEventListener("blur", retire);
    const unsubscribe = client.getQueryCache().subscribe(() => {
      if (attempt.current?.auth !== auth()
        || attempt.current?.instructorId !== client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id) retire();
    });
    return () => {
      document.removeEventListener("focusin", focus, true);
      document.removeEventListener("pointerdown", retire, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("wheel", retire);
      window.removeEventListener("blur", retire);
      unsubscribe();
    };
  }, [client, auth]);
  useLayoutEffect(() => {
    if (initial.current) {
      initial.current = false;
      if (requestedId !== null) arm(search, requestedId);
    }
    const pending = attempt.current;
    if (!pending?.allowed) return;
    if (pending.runId !== runId || pending.auth !== auth()
      || pending.instructorId === undefined || pending.instructorId !== client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id
      || (search !== pending.destination && search !== pending.from)) { pending.allowed = false; return; }
    if (search !== pending.destination) return;
    // Once the destination is committed, leaving it cannot revive this attempt.
    pending.from = pending.destination;
    if (!visible || resolvedId !== pending.id || !document.hasFocus() || document.activeElement !== pending.owner) return;
    const marker = source.current?.querySelector<HTMLElement>(`[data-source-record-id="${pending.id}"]`);
    const inner = marker?.closest<HTMLElement>(".signal-document-scroll--docx");
    const pane = source.current;
    const root = inner && getComputedStyle(inner).overflowY === "visible" && pane ? pane : inner;
    if (!marker || !root) return;
    pending.allowed = false;
    const bounds = root.getBoundingClientRect();
    const target = marker.getBoundingClientRect();
    root.scrollTo({ top: root.scrollTop + target.top - bounds.top - root.clientTop, behavior: "auto" });
    recordSourceReadingPlacement(root);
  });
  return { arm, adopt };
}
