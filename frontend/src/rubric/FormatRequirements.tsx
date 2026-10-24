// F2/F3, screen4e: optional, read-only requirements for one exact active version.
import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { RubricListItem } from "../api/types";
import { useActionLifetime } from "../auth/useActionLifetime";
import { useMe } from "../auth/useAuth";

class FormatIdentityMismatch extends Error {}

function readRequirements(raw: unknown, selected: RubricListItem): string[] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new FormatIdentityMismatch();
  const value = raw as Record<string, unknown>;
  if (!["id", "rubric_family_id", "version", "is_active", "title", "criteria"].every(key => Object.hasOwn(value, key))
    || selected.is_active !== true || value.id !== selected.id || value.rubric_family_id !== selected.rubric_family_id
    || value.version !== selected.version || value.is_active !== true || value.title !== selected.title
    || !Array.isArray(value.criteria) || value.criteria.length !== selected.criteria_count) throw new FormatIdentityMismatch();
  const ids = new Set<number>();
  return value.criteria.map(rawCriterion => {
    if (!rawCriterion || typeof rawCriterion !== "object" || Array.isArray(rawCriterion)) throw new FormatIdentityMismatch();
    const criterion = rawCriterion as Record<string, unknown>;
    if (!Object.hasOwn(criterion, "id") || !Object.hasOwn(criterion, "text")
      || typeof criterion.id !== "number" || !Number.isSafeInteger(criterion.id) || criterion.id <= 0 || ids.has(criterion.id)
      || typeof criterion.text !== "string" || !criterion.text.trim()) throw new FormatIdentityMismatch();
    ids.add(criterion.id);
    return criterion.text;
  });
}

interface RequirementsProps { selected: RubricListItem; onUnconfirmed: () => unknown }

export function FormatRequirements(props: RequirementsProps) {
  const value = props.selected;
  // An unused active version may still be edited. A refreshed receipt retires
  // the loaded preview as well as any pending read for its former metadata.
  return <FormatRequirementsRead key={JSON.stringify([value.id, value.rubric_family_id, value.version, value.title, value.criteria_count, value.is_active])} {...props} />;
}

function FormatRequirementsRead({ selected, onUnconfirmed }: RequirementsProps) {
  const [open, setOpen] = useState(false);
  const scope = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const actor = useMe();
  const actorId = actor.data?.id;
  const isCurrent = useActionLifetime(`format-preview:${selected.id}:${selected.rubric_family_id}:${selected.version}`);
  const query = useQuery({
    queryKey: ["format-requirements", scope, actorId, selected.id, selected.rubric_family_id, selected.version, selected.title, selected.criteria_count, selected.is_active],
    queryFn: async ({ signal }) => {
      if (!isCurrent()) throw new Error("Instructor access changed.");
      const raw = await api.get<unknown>(`/rubrics/${selected.id}`, { signal });
      if (!isCurrent() || signal.aborted) throw new Error("This format preview is no longer current.");
      return readRequirements(raw, selected);
    },
    enabled: open && actorId !== undefined,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  useEffect(() => { if (query.error instanceof FormatIdentityMismatch && isCurrent()) void onUnconfirmed();
    // The current error owns one refresh; ordinary rerenders never repeat it.
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, [query.error, onUnconfirmed]);
  const current = isCurrent() && !actor.error && actorId !== undefined;
  return <section className="signal-format-requirements">
    <button type="button" className="signal-button signal-button--secondary" aria-expanded={open} aria-controls={`${scope}-requirements`} onClick={() => setOpen(value => !value)}>
      {open ? "Hide criteria" : "View criteria"}
    </button>
    {open && <div id={`${scope}-requirements`} className="signal-group-flow">
      <h3 ref={heading} tabIndex={-1}>Requirements in this format</h3>
      {!current ? <p role="status">Instructor access changed. Close this check and sign in again.</p>
        : query.isPending ? <p role="status" aria-busy="true">Loading requirements.</p>
          : query.isError ? <div className="signal-copy-flow" role="alert">
            <p>{query.error instanceof FormatIdentityMismatch
              ? "This format could not be confirmed. Choose a current active format."
              : "Requirements could not be loaded."}</p>
            <button type="button" className="signal-button signal-button--secondary" onClick={event => {
              if (!isCurrent()) return;
              if (document.activeElement === event.currentTarget) heading.current?.focus();
              void query.refetch();
            }}>Retry requirements</button>
          </div>
            : query.data?.length === 0 ? <p>No criteria are recorded in this version.</p>
              : <ol>{query.data?.map((text, index) => <li key={index}>{text}</li>)}</ol>}
    </div>}
  </section>;
}
