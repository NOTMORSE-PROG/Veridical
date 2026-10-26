// F1/F9: assignment reads belong to both an instructor and an auth-query lifetime.
import { useAuthOwner, type AuthOwner } from "../auth/queryOwner";

const lifetimes = new WeakMap<object, number>();
let nextLifetime = 0;

export function groupOwnerKey(owner: AuthOwner): readonly [number | null, number | null] {
  const query = owner.authQuery;
  if (!query || !Number.isSafeInteger(owner.actorId) || (owner.actorId ?? 0) <= 0) return [null, null];
  if (!lifetimes.has(query)) lifetimes.set(query, ++nextLifetime);
  return [owner.actorId!, lifetimes.get(query)!];
}

export function ownedGroupQueryKey(prefix: string, owner: AuthOwner, ...parts: unknown[]) {
  return [prefix, ...groupOwnerKey(owner), ...parts] as const;
}

export function groupOwnerCurrent(owner: AuthOwner) {
  return owner.isCurrent() && owner.authQuery?.state.status === "success";
}

export function useGroupQueryOwner() {
  return useAuthOwner();
}

export async function readOwnedGroup<T>(owner: AuthOwner, read: () => Promise<T>): Promise<T> {
  if (!groupOwnerCurrent(owner)) throw new Error("Instructor access changed. Reopen the group details.");
  const value = await read();
  if (!groupOwnerCurrent(owner)) throw new Error("Instructor access changed. Reopen the group details.");
  return value;
}
