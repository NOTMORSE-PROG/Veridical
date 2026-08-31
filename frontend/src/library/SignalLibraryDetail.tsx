import { useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { LibraryContentPane, type OwnDocumentState } from "./LibraryContentPane";
import { useLibraryDocument, useLibraryItem, usePurgeManuscript } from "./useLibrary";

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

interface SourceStatusCopy {
  heading: string;
  description: string;
  storage: string;
  viewability: string;
  busy: boolean;
}

function ownSourceStatus(
  purgedAt: string | null,
  documentState: OwnDocumentState,
): SourceStatusCopy {
  if (purgedAt || documentState.viewer?.purged_at) {
    const removedAt = purgedAt ?? documentState.viewer?.purged_at;
    return {
      heading: "Stored content removed",
      description: "This manuscript belongs to your account, but its source is no longer stored in the Library.",
      storage: removedAt ? `Removed ${formatDate(removedAt)}` : "Removed",
      viewability: "Cannot be viewed",
      busy: false,
    };
  }
  if (documentState.isPending) {
    return {
      heading: "Checking source availability",
      description: "VERIDICAL is checking whether this stored manuscript can be opened here.",
      storage: "Stored in your Library",
      viewability: "Checking availability",
      busy: true,
    };
  }
  if (documentState.isError || !documentState.viewer) {
    return {
      heading: "Availability not checked",
      description: "VERIDICAL could not check whether this stored manuscript can be opened. Try again in the Source panel.",
      storage: "Stored in your Library",
      viewability: "Check failed",
      busy: false,
    };
  }
  if (!documentState.viewer.available) {
    return {
      heading: "Stored source not viewable",
      description: documentState.viewer.unavailable_reason
        ?? "This manuscript is stored, but a view is not available right now.",
      storage: "Stored in your Library",
      viewability: "View unavailable",
      busy: false,
    };
  }
  return {
    heading: "Full source viewable",
    description: "This manuscript belongs to your account, and its stored source can be opened here.",
    storage: "Stored in your Library",
    viewability: "Available to view",
    busy: false,
  };
}

export function SignalLibraryDetailPage() {
  const params = useParams<{ manuscriptId: string }>();
  const parsedId = Number(params.manuscriptId);
  const manuscriptId = Number.isInteger(parsedId) && parsedId > 0 ? parsedId : undefined;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [activeTab, setActiveTab] = useState<"document" | "details">("document");
  const [confirmingPurge, setConfirmingPurge] = useState(false);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const purge = usePurgeManuscript();
  const { data: item, isPending, isError, refetch } = useLibraryItem(manuscriptId);
  const documentQuery = useLibraryDocument(
    manuscriptId,
    item?.is_own === true && !item.purged_at,
  );
  const ownDocument: OwnDocumentState = {
    viewer: documentQuery.data,
    isPending: documentQuery.isPending,
    isError: documentQuery.isError,
    removedAt: item?.purged_at ?? null,
    onRetry: () => void documentQuery.refetch(),
  };
  const sourceStatus = item?.is_own ? ownSourceStatus(item.purged_at, ownDocument) : null;

  const identity = item
    ? item.title
      ? { primary: item.title, secondary: item.group_label }
      : manuscriptIdentity(item.group_label, item.original_filename)
    : null;

  useRouteFocus(identity ? `${identity.primary} - Manuscript Library - VERIDICAL` : "Manuscript Library - VERIDICAL", headingRef);

  async function confirmPurge() {
    if (!manuscriptId) return;
    setPurgeError(null);
    try {
      await purge.mutateAsync(manuscriptId);
      setConfirmingPurge(false);
    } catch (error) {
      setPurgeError(error instanceof ApiError ? error.message : "Stored content could not be removed. Try again.");
    }
  }

  return (
    <div className="signal-route signal-library-detail">
      <header className="signal-route-header signal-library-detail__header">
        <div>
          <nav className="signal-breadcrumb" aria-label="Breadcrumb">
            <Link to="/library">Manuscript Library</Link>
            <span aria-hidden="true">/</span>
            <span>Record</span>
          </nav>
          <p className="signal-eyebrow">Comparison source record</p>
          <h1 ref={headingRef} tabIndex={-1}>{identity?.primary ?? "Manuscript record"}</h1>
          <p className="signal-route-header__intro">
            {identity?.secondary ?? "Review this record's stored source boundary and metadata."}
          </p>
        </div>
        {item && (
          <span className="signal-library-badge" data-tone={item.is_own ? "own" : "shared"}>
            {item.is_own ? "Your manuscript" : "Bounded shared excerpt"}
          </span>
        )}
      </header>

      {!manuscriptId && (
        <section className="signal-library-state" role="alert">
          <h2>This manuscript address is not valid</h2>
          <p>Return to the Library and open a listed record.</p>
          <ActionLink to="/library" variant="brand">Return to Library</ActionLink>
        </section>
      )}
      {manuscriptId && isPending && <div className="signal-library-state" role="status" aria-live="polite" aria-busy="true">Loading manuscript record.</div>}
      {manuscriptId && isError && (
        <section className="signal-library-state" role="alert">
          <h2>This manuscript could not be loaded</h2>
          <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
        </section>
      )}

      {manuscriptId && item && identity && (
        <section className="signal-library-workbench">
          <div className="signal-library-tabs" role="tablist" aria-label="Manuscript record view">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "document"}
              onClick={() => setActiveTab("document")}
            >
              Source
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "details"}
              onClick={() => setActiveTab("details")}
            >
              Record details
            </button>
          </div>

          <div className="signal-library-workbench__grid">
            <div className="signal-library-source" data-active={activeTab === "document"}>
              <LibraryContentPane
                manuscriptId={manuscriptId}
                isOwn={item.is_own}
                ownDocument={item.is_own ? ownDocument : undefined}
              />
            </div>
            <aside className="signal-library-record" data-active={activeTab === "details"}>
              <div
                role={item.is_own ? "status" : undefined}
                aria-live={item.is_own ? "polite" : undefined}
                aria-atomic={item.is_own ? "true" : undefined}
                aria-busy={item.is_own ? Boolean(sourceStatus?.busy) : undefined}
              >
                <p className="signal-eyebrow">Visibility</p>
                <h2>{sourceStatus?.heading ?? "Excerpt boundary enforced"}</h2>
                <p>
                  {sourceStatus?.description
                    ?? "This record belongs to another instructor. Only bounded chapter excerpts are exposed."}
                </p>
              </div>
              <dl className="signal-library-record__metadata">
                <div>
                  <dt>Ownership</dt>
                  <dd>{item.is_own ? "This manuscript belongs to your account" : "Another instructor's manuscript"}</dd>
                </div>
                <div><dt>Group</dt><dd>{item.group_label}</dd></div>
                <div><dt>Program</dt><dd>{item.program ?? "Not set"}</dd></div>
                <div><dt>Authors</dt><dd>{item.authors.length ? item.authors.join(", ") : "Not listed"}</dd></div>
                <div><dt>Processed</dt><dd>{formatDate(item.created_at)}</dd></div>
                <div>
                  <dt>Storage</dt>
                  <dd>
                    {sourceStatus?.storage
                      ?? (item.purged_at ? `Removed ${formatDate(item.purged_at)}` : "Bounded excerpt retained")}
                  </dd>
                </div>
                <div>
                  <dt>Viewability</dt>
                  <dd>
                    {sourceStatus?.viewability
                      ?? (item.purged_at ? "Cannot be viewed" : "Bounded excerpt only")}
                  </dd>
                </div>
              </dl>
              <div className="signal-library-record__actions">
                <ActionLink to={`/library?compareFrom=${manuscriptId}`} variant="brand">Compare with another record</ActionLink>
                {item.is_own && !item.purged_at && (
                  <Button type="button" variant="danger" onClick={() => setConfirmingPurge(true)}>Remove stored content</Button>
                )}
              </div>
            </aside>
          </div>
        </section>
      )}

      {confirmingPurge && identity && (
        <Dialog
          title={`Remove ${identity.primary}'s stored content?`}
          onClose={purge.isPending ? undefined : () => { setConfirmingPurge(false); setPurgeError(null); }}
          actions={
            <>
              <Button type="button" variant="secondary" disabled={purge.isPending} onClick={() => setConfirmingPurge(false)}>Cancel</Button>
              <Button type="button" variant="danger" busy={purge.isPending} onClick={confirmPurge}>
                {purge.isPending ? "Removing content" : "Remove stored content"}
              </Button>
            </>
          }
        >
          <div className="signal-library-purge-copy">
            <p>The stored file and its future comparison data will be permanently removed.</p>
            <p>Existing check history, readiness reports, instructor decisions, and audit entries remain available.</p>
            <p>This action cannot be undone and never happens automatically.</p>
            {purgeError && <p role="alert">{purgeError}</p>}
          </div>
        </Dialog>
      )}
    </div>
  );
}
