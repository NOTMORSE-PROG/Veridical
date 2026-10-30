// F1/F2/F9: private upload context belongs to one mounted auth lifetime.
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Instructor, SessionStatusOut } from "../api/types";
import { StaleUploadContextError, UploadSessionUnavailableError } from "../api/uploadOutcome";
import { ME_QUERY_KEY } from "./useAuth";

export interface UploadAttemptScope {
  actorId: number;
  isCurrent: () => boolean;
  verify: () => Promise<void>;
  rejectActor: () => void;
}

export function useUploadScope(contextKey?: string) {
  const client = useQueryClient();
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  const [owner] = useState(() => ({
    query: client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }),
    actorId: client.getQueryData<Instructor | null>(ME_QUERY_KEY)?.id,
    contextKey,
  }));
  const alive = useRef(false);
  const lost = useRef(false);
  const attempt = useRef(0);
  const validation = useRef(0);
  const inFlightValidation = useRef<Promise<void> | undefined>(undefined);
  const [status, setStatus] = useState<"checking" | "ready" | "unavailable" | "lost">("checking");
  const ownsContext = useCallback(() => !lost.current && owner.contextKey === currentContext.current
    && owner.query !== undefined
    && owner.actorId !== undefined
    && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === owner.query
    && client.getQueryData<Instructor | null>(ME_QUERY_KEY)?.id === owner.actorId, [client, owner]);
  const isCurrent = useCallback(() => alive.current && ownsContext(), [ownsContext]);
  const invalidate = useCallback(() => {
    lost.current = true;
    attempt.current += 1;
    validation.current += 1;
    if (alive.current) setStatus("lost");
  }, []);
  const verify = useCallback(function verify(afterPending = false, quiet = false): Promise<void> {
    if (inFlightValidation.current && isCurrent()) {
      // A request completion must initiate its own check after the response;
      // a focus read that began during the POST is not that later check.
      return afterPending ? inFlightValidation.current.then(() => verify()) : inFlightValidation.current;
    }
    const run = async () => {
      if (!isCurrent()) { invalidate(); throw new StaleUploadContextError(); }
      const generation = ++validation.current;
      setStatus((previous) => previous === "ready" ? previous : "checking");
      let out: SessionStatusOut;
      try { out = await api.get<SessionStatusOut>("/auth/session"); }
      catch (error) {
        if (!isCurrent() || generation !== validation.current) throw new StaleUploadContextError();
        if (error instanceof ApiError && error.status === 401 && error.code === "unauthenticated") {
          invalidate(); client.clear(); client.setQueryData(ME_QUERY_KEY, null);
          throw new StaleUploadContextError();
        }
        setStatus(previous => quiet && previous === "ready" ? previous : "unavailable");
        throw new UploadSessionUnavailableError();
      }
      if (!isCurrent() || generation !== validation.current) throw new StaleUploadContextError();
      if (!out || !(out.instructor === null || (typeof out.instructor === "object"
        && Number.isSafeInteger(out.instructor.id) && out.instructor.id > 0))) {
        setStatus("unavailable"); throw new UploadSessionUnavailableError();
      }
      if (out.instructor?.id !== owner.actorId) {
        invalidate();
        client.clear();
        client.setQueryData(ME_QUERY_KEY, out.instructor);
        throw new StaleUploadContextError();
      }
      setStatus("ready");
    };
    const pending = run();
    inFlightValidation.current = pending;
    void pending.finally(() => {
      if (inFlightValidation.current === pending) inFlightValidation.current = undefined;
    }).catch(() => {});
    return pending;
  }, [client, invalidate, isCurrent, owner]);
  useLayoutEffect(() => { if (alive.current && !isCurrent()) invalidate(); }, [contextKey, invalidate, isCurrent]);
  useLayoutEffect(() => {
    alive.current = true;
    const unsubscribe = client.getQueryCache().subscribe(() => {
      if (!isCurrent()) invalidate();
    });
    const check = () => { if (document.visibilityState !== "hidden") void verify(false, true).catch(() => {}); };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    void verify().catch(() => {});
    return () => {
      alive.current = false;
      attempt.current += 1;
      validation.current += 1;
      // StrictMode replays setup/cleanup on the same mounted hook. The next
      // setup must not coalesce with this retired effect's pending read.
      inFlightValidation.current = undefined;
      unsubscribe();
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, [client, invalidate, isCurrent, verify]);
  function capture(): UploadAttemptScope {
    if (!isCurrent() || status !== "ready" || owner.actorId === undefined) throw new StaleUploadContextError();
    const generation = ++attempt.current;
    const current = () => isCurrent() && attempt.current === generation;
    return {
      actorId: owner.actorId, isCurrent: current,
      async verify() { await verify(true); if (!current()) throw new StaleUploadContextError(); },
      rejectActor() {
        if (!current()) return;
        invalidate();
        client.clear();
        // Establishing the actual session is the existing auth query's job;
        // no old File is adopted by the next actor.
        client.setQueryData(ME_QUERY_KEY, null);
      },
    };
  }
  return { status, ownsContext: ownsContext(), isCurrent, verify, capture, actorId: owner.actorId };
}

export function rejectChangedUploadActor(error: unknown, scope: UploadAttemptScope) {
  if (error instanceof ApiError && ((error.status === 409 && error.code === "actor_context_changed")
    || (error.status === 401 && error.code === "unauthenticated"))) scope.rejectActor();
}

export async function verifyUploadFailure(error: unknown, scope: UploadAttemptScope): Promise<never> {
  if (!scope.isCurrent()) throw new StaleUploadContextError();
  rejectChangedUploadActor(error, scope);
  if (!scope.isCurrent()) throw new StaleUploadContextError();
  try { await scope.verify(); }
  catch (sessionError) {
    if (sessionError instanceof StaleUploadContextError) throw sessionError;
    // Retain the original cause and creation certainty while the form is
    // hidden pending session recovery. This never starts another upload.
  }
  throw error;
}
