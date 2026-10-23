// F7/F9, screen 4aa: one mounted owner and one serial private write at a time.
import { useLayoutEffect, useRef, useState } from "react";
import { StaleUploadContextError } from "../api/uploadOutcome";
import { useUploadScope, type UploadAttemptScope } from "../auth/useUploadScope";

export interface LibraryWriteOperation {
  kind: "batch" | "decision" | "remove";
  stage: "checking" | "sent";
  scope: UploadAttemptScope;
  isCurrent: () => boolean;
  ownsContinuation: () => boolean;
  admitted: () => void;
}

export function useLibraryUploadOwner() {
  const session = useUploadScope();
  const active = useRef<LibraryWriteOperation | undefined>(undefined);
  const [, refresh] = useState(0);
  useLayoutEffect(() => {
    if (!session.ownsContext) {
      active.current = undefined;
      refresh(value => value + 1);
    }
  }, [session.ownsContext]);
  useLayoutEffect(() => () => { active.current = undefined; }, []);

  function begin(kind: LibraryWriteOperation["kind"]): LibraryWriteOperation | undefined {
    if (active.current || !session.ownsContext || session.status !== "ready" || !session.isCurrent()) return;
    const captured = session.capture();
    const current = () => active.current === operation && captured.isCurrent();
    const operation: LibraryWriteOperation = {
      kind, stage: "checking", isCurrent: current, ownsContinuation: captured.isCurrent,
      scope: {
        actorId: captured.actorId, isCurrent: current,
        async verify() {
          if (!current()) throw new StaleUploadContextError();
          await captured.verify();
          if (!current()) throw new StaleUploadContextError();
        },
        rejectActor: () => { if (current()) captured.rejectActor(); },
      },
      admitted() {
        if (!current()) throw new StaleUploadContextError();
        operation.stage = "sent";
        refresh(value => value + 1);
      },
    };
    active.current = operation;
    refresh(value => value + 1);
    return operation;
  }

  function finish(operation: LibraryWriteOperation) {
    if (active.current !== operation) return;
    const owned = operation.isCurrent();
    active.current = undefined;
    if (owned) refresh(value => value + 1);
  }

  function cancelPreflight() {
    const operation = active.current;
    if (operation?.stage === "sent") return false;
    if (operation) finish(operation);
    return true;
  }

  function retire() {
    const owned = session.isCurrent();
    active.current = undefined;
    if (owned) refresh(value => value + 1);
  }

  return { session, operation: active.current, busy: Boolean(active.current), begin, finish, cancelPreflight, retire };
}
