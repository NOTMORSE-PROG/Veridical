// Screen 4w-Detail (V-066) — one library record: content pane (own
// document, bounded excerpt, or purged notice -- see LibraryContentPane)
// plus a metadata card. Mirrors DocumentViewerPage's layout, minus the
// analysis pane (a library record isn't a check run, so there are no
// flags to browse).
import { useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { Chip } from "../components/Chip";
import { Modal, ModalBackdrop } from "../components/Modal";
import { StatusPill } from "../components/StatusPill";
import { TabPanel, Tabs } from "../components/Tabs";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { LibraryContentPane } from "./LibraryContentPane";
import { useLibraryItem, usePurgeManuscript } from "./useLibrary";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

export function LibraryDetailPage() {
  const { manuscriptId } = useParams<{ manuscriptId: string }>();
  const id = Number(manuscriptId);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [activeTab, setActiveTab] = useState<"document" | "details">("document");
  const [confirmingPurge, setConfirmingPurge] = useState(false);
  const [purgeError, setPurgeError] = useState<string | null>(null);
  const purge = usePurgeManuscript();

  const { data: item, isPending, isError, refetch } = useLibraryItem(id);
  useRouteFocus(item ? `${item.title ?? item.group_label} - Library - VERIDICAL` : "Library - VERIDICAL", headingRef);

  const identity = item
    ? item.title
      ? { primary: item.title, secondary: item.group_label }
      : manuscriptIdentity(item.group_label, item.original_filename)
    : null;

  async function confirmPurge() {
    setPurgeError(null);
    try {
      await purge.mutateAsync(id);
      setConfirmingPurge(false);
    } catch (err) {
      setPurgeError(
        err instanceof ApiError ? err.message : "Could not remove this manuscript's content. Try again.",
      );
    }
  }

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col">
      {/* ux-critic finding (V-066 review, 2026-08-23): the heading used to
          be mounted only AFTER a loading/error early-return, so
          `useRouteFocus`'s one focus attempt (fired before `item` loads)
          always found a null ref -- this screen never actually moved
          focus on navigation. Always rendering the heading first, with a
          generic fallback while `item` loads, fixes it (same root cause
          and fix as Library.tsx). */}
      <header className="flex flex-col gap-1 border-b border-border px-4 py-2.5 sm:px-6">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-ink-tertiary">
          <Link to="/library" className="text-link underline hover:text-link-hover">
            Library
          </Link>
          {identity && (
            <>
              <span aria-hidden="true">/</span>
              <span>{identity.primary}</span>
            </>
          )}
        </nav>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink">
          {identity?.primary ?? "Manuscript"}
        </h1>
        {identity?.secondary && <p className="text-sm text-ink-secondary">{identity.secondary}</p>}
      </header>

      {isPending && (
        <div role="status" aria-live="polite" aria-busy="true" className="p-6 text-sm text-ink-secondary">
          Loading manuscript.
        </div>
      )}
      {(isError || (!isPending && !item)) && (
        <div role="alert" className="p-6 text-sm text-status-attention-text">
          This manuscript couldn't be loaded.{" "}
          <button type="button" onClick={() => refetch()} className="font-medium underline">
            Try again
          </button>
          .
        </div>
      )}
      {item && identity && (
        <>
      <Tabs
        label="Library record view"
        className="lg:hidden"
        active={activeTab}
        onChange={(id2) => setActiveTab(id2 as "document" | "details")}
        tabs={[
          { id: "document", label: "Document" },
          { id: "details", label: "Details" },
        ]}
      />
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-1">
        <TabPanel
          id="document"
          label="Document"
          active={activeTab === "document"}
          className="lg:block min-h-0 border-b border-border lg:border-r lg:border-b-0"
        >
          <LibraryContentPane manuscriptId={id} isOwn={item.is_own} />
        </TabPanel>
        <TabPanel
          id="details"
          label="Details"
          active={activeTab === "details"}
          className="lg:block min-h-0 overflow-y-auto"
        >
          <div className="flex flex-col gap-3 p-4 text-sm">
            <dl className="flex flex-col gap-2">
              <div>
                <dt className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Group</dt>
                <dd className="text-ink">{item.group_label}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Program</dt>
                <dd className="text-ink">{item.program ?? "Not set"}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Authors</dt>
                <dd className="text-ink">{item.authors.length > 0 ? item.authors.join(", ") : "Not listed"}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Ingested</dt>
                <dd className="text-ink">{formatDate(item.created_at)}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Whose</dt>
                <dd>
                  <StatusPill tone={item.is_own ? "info" : "neutral"}>
                    {item.is_own ? "Yours" : "Another instructor's"}
                  </StatusPill>
                </dd>
              </div>
              <div>
                <dt className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">Archive</dt>
                <dd>
                  {item.purged_at ? (
                    <Chip>Content removed {formatDate(item.purged_at)}</Chip>
                  ) : (
                    <Chip>Content stored</Chip>
                  )}
                </dd>
              </div>
            </dl>
            {item.is_own && !item.purged_at && (
              <button
                type="button"
                onClick={() => setConfirmingPurge(true)}
                className="w-fit text-sm font-medium text-danger underline hover:text-danger-hover"
              >
                Purge stored content
              </button>
            )}
            <Link
              to={`/library?compareFrom=${id}`}
              className="w-fit text-sm font-medium text-link underline hover:text-link-hover"
            >
              Compare with another manuscript
            </Link>
          </div>
        </TabPanel>
      </div>

      {confirmingPurge && (
        <ModalBackdrop>
          <Modal
            title={`Remove ${identity.primary}'s stored content?`}
            onClose={purge.isPending ? undefined : () => setConfirmingPurge(false)}
            footer={
              <>
                <button
                  type="button"
                  onClick={() => setConfirmingPurge(false)}
                  disabled={purge.isPending}
                  className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={confirmPurge}
                  disabled={purge.isPending}
                  className="flex h-11 items-center justify-center rounded-md bg-danger px-4 text-sm font-bold text-on-danger hover:bg-danger-hover disabled:opacity-60"
                >
                  {purge.isPending ? "Removing" : "Remove stored content"}
                </button>
              </>
            }
          >
            <div className="flex flex-col gap-3 text-sm" aria-busy={purge.isPending}>
              <p className="text-ink">
                This deletes the file VERIDICAL stored for this manuscript, and the comparison data
                used to check future manuscripts against it.
              </p>
              <p className="text-ink-secondary">
                Its check history and any decision you've already made stay exactly as they are.
                This does not undo a decision or remove this manuscript from your records.
              </p>
              <p className="text-ink-secondary">
                This cannot be undone. VERIDICAL never removes content automatically. This only
                happens when you choose it here.
              </p>
              {purgeError && (
                <p role="alert" className="text-sm font-medium text-danger">
                  <span className="sr-only">Error: </span>
                  {purgeError}
                </p>
              )}
            </div>
          </Modal>
        </ModalBackdrop>
      )}
        </>
      )}
    </div>
  );
}
