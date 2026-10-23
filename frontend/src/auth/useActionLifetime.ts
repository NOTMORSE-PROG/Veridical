import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";
import { ME_QUERY_KEY } from "./useAuth";

/** A dialog's completion cannot affect a replacement authenticated lifetime. */
export function useActionLifetime(purpose?: string) {
  const client = useQueryClient();
  const [owner] = useState(() => ({
    purpose,
    query: client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }),
    instructor: client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id,
  }));
  const alive = useRef(false);
  const currentPurpose = useRef(purpose);
  const replaced = useRef(false);
  if (currentPurpose.current !== purpose) replaced.current = true;
  currentPurpose.current = purpose;
  useLayoutEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  return () => alive.current && !replaced.current && owner.purpose === currentPurpose.current
    && owner.query !== undefined && owner.instructor !== undefined
    && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === owner.query
    && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === owner.instructor;
}
