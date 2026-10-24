import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useMemo, useSyncExternalStore } from "react";
import { ME_QUERY_KEY } from "./useAuth";

/** A new authentication query is a new lifetime, even for the same instructor. */
export function captureAuthOwner(client: QueryClient) {
  const authQuery = client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true });
  const actorId = client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id;
  return { authQuery, actorId, isCurrent: () => authQuery !== undefined && actorId !== undefined
    && client.getQueryCache().find({ queryKey: ME_QUERY_KEY, exact: true }) === authQuery
    && client.getQueryData<{ id: number } | null>(ME_QUERY_KEY)?.id === actorId };
}

/** Observe the existing authentication cache without starting another read. */
export function useAuthOwner() {
  const client = useQueryClient();
  const cache = client.getQueryCache();
  const state = useSyncExternalStore(listener => cache.subscribe(event => {
    if (event.query.queryKey.length === ME_QUERY_KEY.length
      && event.query.queryKey.every((part: unknown, index: number) => part === ME_QUERY_KEY[index])) listener();
  }),
    () => cache.find({ queryKey: ME_QUERY_KEY, exact: true })?.state);
  const query = cache.find({ queryKey: ME_QUERY_KEY, exact: true });
  const actorId = (state?.data as { id?: number } | null)?.id;
  return useMemo(() => ({ ...captureAuthOwner(client), authQuery: query, actorId }), [client, query, actorId]);
}

export type AuthOwner = ReturnType<typeof captureAuthOwner>;
