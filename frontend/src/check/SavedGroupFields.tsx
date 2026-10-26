// F1, screens4a/4e: text narrows records; only an explicit radio selects identity.
import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { validateSavedGroup, type SavedGroup } from "../api/groupAssignmentOutcome";
import type { CurrentGroup, GroupOut } from "../api/types";
import { ProposalActions } from "./GroupProposalFields";
import { groupOwnerCurrent, ownedGroupQueryKey, readOwnedGroup, useGroupQueryOwner } from "./groupQueryOwner";

export const groupSecondary = "flex min-h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45";
export const groupPrimary = "flex min-h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover disabled:opacity-60";

export function SavedGroupFacts({ group }: { group: CurrentGroup }) {
  return <dl className="flex flex-col gap-3 text-sm text-ink">
    <div><dt className="font-bold">Group name</dt><dd className="break-words">{group.name} <span className="text-ink-tertiary">Group #{group.id}</span></dd></div>
    <div><dt className="font-bold">Program</dt><dd>{group.program ?? "Not set"}</dd></div>
    <div><dt className="font-bold">Group members</dt><dd>{group.member_names.length ? <ul>{group.member_names.map((name, index) => <li key={index} className="break-words">{name}</li>)}</ul> : "No members recorded."}</dd></div>
    <div><dt className="font-bold">Saved title</dt><dd className="break-words">{group.title ?? "Not recorded"}</dd></div>
  </dl>;
}

function validateGroups(value: unknown): GroupOut[] {
  if (!Array.isArray(value)) throw new Error("Could not verify saved groups.");
  const ids = new Set<number>();
  for (const group of value) {
    if (!group || typeof group !== "object" || !Number.isSafeInteger(group.id) || group.id <= 0
      || ids.has(group.id) || typeof group.name !== "string" || !group.name.trim()
      || !(group.program === null || typeof group.program === "string")) throw new Error("Could not verify saved groups.");
    ids.add(group.id);
  }
  return value as GroupOut[];
}

export function SavedGroupFields({ active, locked, actionsTarget, onCancel, cancelLabel, onManual, onAssign, needsReview, onReviewed }: {
  active: boolean; locked: boolean; actionsTarget?: HTMLElement | null;
  onCancel: () => void; cancelLabel: string; onManual: () => void;
  onAssign: (reviewed: SavedGroup) => void;
  needsReview: boolean; onReviewed: () => void;
}) {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [reviewed, setReviewed] = useState<SavedGroup | undefined>();
  const owner = useGroupQueryOwner();
  const explicitReviewPending = useRef(false);
  const searchId = useId();
  const listId = useId();
  const list = useQuery({ queryKey: ownedGroupQueryKey("groups", owner), queryFn: () => readOwnedGroup(owner, async () => validateGroups(await api.get<unknown>("/groups"))), enabled: active && groupOwnerCurrent(owner), retry: false });
  const details = useQuery({
    queryKey: ownedGroupQueryKey("saved-group", owner, selectedId),
    queryFn: () => readOwnedGroup(owner, async () => validateSavedGroup(await api.get<unknown>(`/groups/${selectedId}`), selectedId!)),
    enabled: active && selectedId !== null && groupOwnerCurrent(owner), retry: false,
  });
  useEffect(() => {
    if (!reviewed && details.isSuccess && !details.isFetching && details.data.id === selectedId) {
      setReviewed(structuredClone(details.data));
      if (explicitReviewPending.current) { explicitReviewPending.current = false; onReviewed(); }
    }
  }, [details.data, details.isFetching, details.isSuccess, onReviewed, reviewed, selectedId]);

  const needle = search.trim().toLocaleLowerCase();
  const results = (list.data ?? []).filter((group) => `${group.name} ${group.program ?? ""} ${group.id}`.toLocaleLowerCase().includes(needle));
  return <section hidden={!active} inert={!active} className={active ? "flex flex-col gap-3" : "hidden"} aria-label="Saved group selection">
    <div className="flex flex-col gap-2">
      <label htmlFor={searchId} className="text-sm font-bold text-ink">Search saved groups</label>
      <input id={searchId} type="search" value={search} disabled={locked} onChange={(event) => setSearch(event.target.value)} className="min-h-11 rounded-md border border-border-input bg-panel px-3 text-base text-ink" />
    </div>
    {list.isPending && <p role="status">Loading saved groups.</p>}
    {list.isError && <div role="alert" className="flex flex-col gap-2 text-sm text-danger"><p>Could not load your saved groups.</p><button type="button" disabled={locked || list.isFetching} onClick={() => { void list.refetch(); }} className="w-fit font-bold underline">Try loading saved groups again</button></div>}
    {list.isSuccess && !list.data.length && <div className="flex flex-col gap-2 text-sm text-ink"><p>No saved groups are available.</p><button type="button" disabled={locked} onClick={onManual} className="w-fit font-bold underline">Enter group details</button></div>}
    {list.isSuccess && list.data.length > 0 && <fieldset disabled={locked} className="flex flex-col gap-2">
      <legend className="mb-2 text-sm font-bold text-ink">Choose a saved group</legend>
      {results.map((group) => <label key={group.id} className="flex min-h-11 items-start gap-3 rounded-md border border-border p-3 text-sm text-ink">
        <input type="radio" name={listId} value={group.id} checked={selectedId === group.id} onChange={() => { explicitReviewPending.current = true; setSelectedId(group.id); setReviewed(undefined); }} className="mt-1" />
        <span className="min-w-0 break-words"><b>{group.name}</b>{" "}<span className="block text-ink-secondary">{group.program ?? "Program not set"} · Group #{group.id}</span></span>
      </label>)}
      {!results.length && <div className="flex flex-col gap-2 text-sm"><p>No saved groups match this search.</p><button type="button" onClick={() => setSearch("")} className="w-fit font-bold underline">Clear search</button></div>}
    </fieldset>}
    {selectedId !== null && <section className="flex flex-col gap-3 rounded-md border border-border bg-status-neutral-bg p-3" aria-label="Selected saved group">
      <h3 className="text-sm font-bold text-ink">Selected saved group</h3>
      {!reviewed && details.isFetching && <p role="status">Loading the selected group's details.</p>}
      {!reviewed && details.isError && <div role="alert" className="flex flex-col gap-2 text-sm text-danger"><p>Could not load details for Group #{selectedId}.</p><button type="button" disabled={locked || details.isFetching} onClick={() => { void details.refetch(); }} className="w-fit font-bold underline">Try loading selected group again</button></div>}
      {reviewed && <><SavedGroupFacts group={reviewed} /><p className="text-sm text-ink-secondary">This uses the saved group record. Its members, program and title will stay unchanged.</p></>}
      <button type="button" disabled={locked || details.isFetching} onClick={() => { explicitReviewPending.current = true; setReviewed(undefined); void details.refetch(); }} className="w-fit text-sm font-bold text-link underline">Refresh selected group details</button>
    </section>}
    {active && <ProposalActions target={actionsTarget}>
      <button type="button" disabled={locked} onClick={onCancel} className={groupSecondary}>{cancelLabel}</button>
      <button type="button" disabled={locked || !reviewed || details.isFetching || needsReview} onClick={() => { if (active && !locked && reviewed && !needsReview) onAssign(reviewed); }} className={groupPrimary}>Assign to selected group</button>
    </ProposalActions>}
  </section>;
}
