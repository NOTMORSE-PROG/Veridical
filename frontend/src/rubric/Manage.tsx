// Screen 4m — Rubric management: versions (F2.4). A version list per
// family: activate any version, view any version's criteria (read-only
// for superseded ones, ReviewCriteria.tsx), delete a version blocked
// when reports are pinned to it. F2.5 (criterion library import) is
// proposed-only in FEATURES.md — not built here.
//
// V-055 rebuild: the summary bar used to render a hardcoded "Active"
// pill regardless of whether the shown family's shown version was
// actually active — found live against the demo account's real data
// (a genuinely-unconfirmed draft family rendered "v1 * Active"). Fixed
// by deriving status from the real data instead of asserting it
// (ground rule 1/3). Also: a real delete confirmation (previously a
// single misclick destroyed a version), a dual desktop-table/mobile-
// card layout (the old 6-column grid overflowed the page at 375px,
// WCAG 1.4.10), 44px touch targets on mobile (WCAG 2.5.8), route focus
// management, and an honest multi-family disclosure (only the first
// family shows; the backend already supports more).
//
// Honest scope note: a multi-family switcher isn't built — this shows
// one family only, and says so when more than one exists. Two families
// coexisting is proven at the data/API layer (list_rubric_families
// returns one row per family, tested), but nothing in this screen lets
// an instructor switch between them yet.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import type { RubricListItem } from "../api/types";
import { Chip } from "../components/Chip";
import { Modal, ModalBackdrop } from "../components/Modal";
import type { StatusPillTone } from "../components/StatusPill";
import { StatusPill } from "../components/StatusPill";
import { useRouteFocus } from "../routing/useRouteFocus";
import { RerunModal } from "./RerunModal";
import {
  useActivateRubric,
  useDeleteRubric,
  useRubricFamilies,
  useRubricVersions,
} from "./useRubric";
import { UploadRubricModal } from "./UploadRubricModal";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function rowStatus(
  version: RubricListItem,
  maxVersion: number | undefined,
): { label: string; tone: StatusPillTone } {
  if (version.is_active) return { label: "Active", tone: "success" };
  if (version.version === maxVersion) return { label: "Draft", tone: "attention" };
  return { label: "Superseded", tone: "neutral" };
}

interface DeleteTarget {
  id: number;
  version: number;
  title: string;
}

interface JustChanged {
  version: number;
  verb: "deleted" | "activated";
}

function DeleteConfirmModal({
  target,
  isPending,
  error,
  onCancel,
  onConfirm,
}: {
  target: DeleteTarget;
  isPending: boolean;
  error: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <ModalBackdrop>
      <Modal
        title={`Delete version ${target.version}?`}
        onClose={isPending ? undefined : onCancel}
        footer={
          <>
            <button
              type="button"
              onClick={onCancel}
              disabled={isPending}
              className="flex h-11 items-center justify-center rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg disabled:opacity-45"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={isPending}
              className="flex h-11 items-center justify-center rounded-md bg-danger px-4 text-sm font-bold text-on-danger hover:bg-danger-hover disabled:opacity-60"
            >
              {isPending ? "Deleting" : "Delete version"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3 text-sm" aria-busy={isPending}>
          <p className="text-ink">
            You are about to permanently delete <b>{target.title}</b>, version {target.version}.
            This cannot be undone.
          </p>
          <p className="text-ink-secondary">
            It has no reports pinned to it, so no existing readiness report will be affected.
          </p>
          {isPending && (
            <p role="status" className="text-sm text-ink-secondary">
              Deleting version {target.version}.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm font-medium text-danger">
              <span className="sr-only">Error: </span>
              {error}
            </p>
          )}
        </div>
      </Modal>
    </ModalBackdrop>
  );
}

const desktopActionLinkClass = "inline-flex min-h-8 items-center text-sm font-medium underline";
const mobileActionLinkClass = "inline-flex min-h-11 items-center text-sm font-medium underline";

function VersionActions({
  version,
  status,
  onActivate,
  onDelete,
  activatePending,
  linkClass,
  staticClass,
}: {
  version: RubricListItem;
  status: { label: string; tone: StatusPillTone };
  onActivate: () => void;
  onDelete: () => void;
  activatePending: boolean;
  linkClass: string;
  staticClass: string;
}) {
  const reviewLabel = status.label === "Draft" ? "Review" : "View";
  return (
    <>
      <Link to={`/rubric/${version.id}/review`} className={`${linkClass} text-link hover:text-link-hover`}>
        {reviewLabel}
      </Link>
      {status.label !== "Active" && (
        <button
          type="button"
          onClick={onActivate}
          disabled={activatePending}
          className={`${linkClass} text-link hover:text-link-hover disabled:opacity-45`}
        >
          Activate
        </button>
      )}
      {status.label !== "Active" &&
        (version.report_count > 0 ? (
          <span className={staticClass}>Reports pinned</span>
        ) : (
          <button
            type="button"
            onClick={onDelete}
            className={`${linkClass} text-danger hover:text-danger-hover`}
          >
            Delete
          </button>
        ))}
    </>
  );
}

export function ManageRubricPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Rubric - VERIDICAL", headingRef);

  const { data: families, isPending, isError, refetch } = useRubricFamilies();
  const activeFamilies = (families ?? []).filter((f) => f.is_active);
  const family = activeFamilies[0] ?? families?.[0];
  const { data: versions } = useRubricVersions(family?.rubric_family_id);
  const activate = useActivateRubric();
  const del = useDeleteRubric();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState("");
  const [justChanged, setJustChanged] = useState<JustChanged | null>(null);
  const historyHeadingRef = useRef<HTMLHeadingElement>(null);
  // V-041: a persistent (not one-shot) signal that a version was just
  // activated -- unlike `justChanged` (nulled the same tick it's read,
  // only driving a one-time focus+announce effect), this survives until
  // explicitly dismissed or acted on, since an instructor who doesn't
  // re-run manuscripts in that exact moment (e.g. triages first, comes
  // back later) would otherwise have no way to rediscover the offer.
  const [recentActivation, setRecentActivation] = useState<number | null>(null);
  const [rerunOpen, setRerunOpen] = useState(false);

  useEffect(() => {
    if (!justChanged) return;
    historyHeadingRef.current?.focus();
    setLiveMessage(
      justChanged.verb === "deleted"
        ? `Version ${justChanged.version} deleted.`
        : `Version ${justChanged.version} is now active.`,
    );
    setJustChanged(null);
  }, [justChanged]);

  async function handleActivate(id: number, version: number) {
    setActionError(null);
    try {
      await activate.mutateAsync(id);
      setJustChanged({ version, verb: "activated" });
      setRecentActivation(version);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Activate failed. Try again.");
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleteError(null);
    try {
      await del.mutateAsync(deleteTarget.id);
      setJustChanged({ version: deleteTarget.version, verb: "deleted" });
      setDeleteTarget(null);
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Delete failed. Try again.");
    }
  }

  if (isPending) {
    return (
      <div className="flex flex-col gap-4 p-4 sm:p-6">
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Rubric
        </h1>
        <p role="status" aria-live="polite" aria-busy="true" className="text-sm text-ink-tertiary">
          Loading your rubric.
        </p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col gap-4 p-4 sm:p-6">
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Rubric
        </h1>
        <div
          role="alert"
          className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text"
        >
          Could not load your required format.{" "}
          <button type="button" onClick={() => refetch()} className="underline">
            Try again
          </button>
          .
        </div>
      </div>
    );
  }

  if (!family) {
    return (
      <div className="flex flex-col gap-4 p-4 sm:p-6">
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Rubric
        </h1>
        <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed border-border-input bg-page p-8 text-center">
          <h2 className="text-md font-bold text-ink">No required format yet</h2>
          <p className="max-w-md text-sm text-ink-secondary">
            Upload the rubric or format document (PDF or DOCX) to get started. VERIDICAL parses it
            into criteria for your review before anything is checked.
          </p>
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            className="mt-1 flex h-11 items-center justify-center rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
          >
            Upload required format
          </button>
        </div>
        {uploadOpen && <UploadRubricModal onClose={() => setUploadOpen(false)} />}
      </div>
    );
  }

  const maxVersion = versions?.[0]?.version;
  const activeVersion = versions?.find((v) => v.is_active);
  const hasActive = activeVersion !== undefined;
  const displayVersion = activeVersion ?? versions?.[0] ?? family;
  const multiFamily = (families?.length ?? 0) > 1;

  return (
    <div className="flex flex-col gap-4 p-4 sm:p-6">
      <div>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink sm:text-xl">
          Rubric
        </h1>
        <p className="text-sm text-ink-secondary">Manage your required format and its version history.</p>
      </div>

      <p role="status" aria-live="polite" className="sr-only">
        {liveMessage}
      </p>

      {multiFamily && (
        <p className="rounded-md border border-border bg-status-neutral-bg px-3 py-2 text-sm text-ink-secondary">
          VERIDICAL found {families?.length} required formats on your account. Showing{" "}
          <b className="font-semibold text-ink">{displayVersion.title}</b>; switching between
          formats is not available yet.
        </p>
      )}

      <section
        aria-labelledby="format-summary-heading"
        className="flex flex-col gap-3 rounded-lg border border-border bg-panel p-4 shadow-sm sm:p-5"
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2.5">
            <span id="format-summary-heading" className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
              {hasActive ? "Active format" : "This format"}
            </span>
            <Chip maxWidthClass="max-w-[260px]" title={displayVersion.title}>
              {displayVersion.title}
            </Chip>
            <StatusPill tone={hasActive ? "success" : "attention"}>
              {hasActive ? `v${displayVersion.version} · Active` : `v${displayVersion.version} · Not confirmed`}
            </StatusPill>
            <Chip>
              {displayVersion.criteria_count} {displayVersion.criteria_count === 1 ? "criterion" : "criteria"}
            </Chip>
          </div>
          <div className="flex flex-none flex-wrap items-center gap-2">
            {hasActive ? (
              <>
                <Link
                  to={`/rubric/${displayVersion.id}/review`}
                  className="flex h-11 items-center justify-center whitespace-nowrap rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
                >
                  View criteria
                </Link>
                <button
                  type="button"
                  onClick={() => setRerunOpen(true)}
                  className="flex h-11 items-center justify-center whitespace-nowrap rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
                >
                  Re-run manuscripts
                </button>
                <button
                  type="button"
                  onClick={() => setUploadOpen(true)}
                  className="flex h-11 items-center justify-center whitespace-nowrap rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
                >
                  Upload new format
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => setUploadOpen(true)}
                  className="flex h-11 items-center justify-center whitespace-nowrap rounded-md border border-border-input bg-panel px-4 text-sm font-bold text-ink hover:bg-status-neutral-bg"
                >
                  Upload new format
                </button>
                <Link
                  to={`/rubric/${displayVersion.id}/review`}
                  className="flex h-11 items-center justify-center whitespace-nowrap rounded-md bg-action px-4 text-sm font-bold text-on-action hover:bg-action-hover"
                >
                  Review and activate
                </Link>
              </>
            )}
          </div>
        </div>
        {!hasActive && (
          <p className="rounded-md bg-status-attention-bg px-3 py-2 text-sm text-status-attention-text">
            No version of this format is active yet. Review and confirm a version before VERIDICAL
            can check manuscripts against it.
          </p>
        )}
      </section>

      {recentActivation !== null && (
        <div
          role="status"
          className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-status-info-bg px-3 py-2.5 text-sm text-status-info-text"
        >
          <span>
            Version {recentActivation} is now active. Manuscripts already checked under a
            previous version can be re-run against it.
          </span>
          <div className="flex flex-none items-center gap-3">
            <button
              type="button"
              onClick={() => {
                setRerunOpen(true);
                setRecentActivation(null);
              }}
              className="text-sm font-semibold underline hover:no-underline"
            >
              Re-run manuscripts
            </button>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setRecentActivation(null)}
              className="flex h-8 w-8 flex-none items-center justify-center rounded-md hover:bg-status-neutral-bg"
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="5" y1="5" x2="19" y2="19" />
                <line x1="19" y1="5" x2="5" y2="19" />
              </svg>
            </button>
          </div>
        </div>
      )}

      {rerunOpen && <RerunModal onClose={() => setRerunOpen(false)} />}

      {actionError && (
        <div
          role="alert"
          className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-3 text-sm font-medium text-status-attention-text"
        >
          {actionError}
        </div>
      )}

      <h2
        ref={historyHeadingRef}
        tabIndex={-1}
        id="version-history-heading"
        className="text-md font-bold text-ink"
      >
        Version history
      </h2>

      {/* Mobile: card-per-row, 44px touch targets (WCAG 2.5.8). The fixed
          desktop columns below need ~750px before content even starts, so
          this stays a card list all the way to `lg:` (measured against
          AppShell's own 1007-1009px shell-breakpoint finding), not the
          narrower `sm:` most other screens switch at. */}
      <ul aria-labelledby="version-history-heading" className="flex flex-col gap-2 lg:hidden">
        {versions?.map((version) => {
          const status = rowStatus(version, maxVersion);
          return (
            <li key={version.id} className="rounded-lg border border-border bg-panel p-3">
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink" title={version.title}>
                  {version.title}
                </span>
                <StatusPill tone={status.tone}>{status.label}</StatusPill>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-tertiary">
                <span>v{version.version}</span>
                <span>
                  {version.criteria_count} {version.criteria_count === 1 ? "criterion" : "criteria"}
                </span>
                <span>{formatDate(version.created_at)}</span>
                <span>{version.report_count > 0 ? `${version.report_count} pinned` : "No reports yet"}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                <VersionActions
                  version={version}
                  status={status}
                  onActivate={() => handleActivate(version.id, version.version)}
                  onDelete={() => setDeleteTarget(version)}
                  activatePending={activate.isPending}
                  linkClass={mobileActionLinkClass}
                  staticClass="inline-flex min-h-11 items-center text-sm text-ink-tertiary"
                />
              </div>
            </li>
          );
        })}
        {versions?.length === 0 && (
          <li className="rounded-lg border border-border bg-panel p-4 text-sm text-ink-tertiary">
            No versions yet.
          </li>
        )}
      </ul>

      {/* Desktop: the grid table. */}
      <div
        role="table"
        aria-labelledby="version-history-heading"
        className="hidden overflow-hidden rounded-lg border border-border lg:block"
      >
        <div
          role="row"
          className="grid grid-cols-[64px_120px_minmax(0,1fr)_72px_100px_104px_minmax(190px,auto)] gap-3 border-b border-border bg-status-neutral-bg px-4 py-2.5 text-xs font-semibold tracking-header text-ink-tertiary uppercase"
        >
          <span role="columnheader">Version</span>
          <span role="columnheader">Status</span>
          <span role="columnheader">File</span>
          <span role="columnheader">Criteria</span>
          <span role="columnheader">Uploaded</span>
          <span role="columnheader">Reports</span>
          <span role="columnheader">
            <span className="sr-only">Actions</span>
          </span>
        </div>
        {versions?.map((version) => {
          const status = rowStatus(version, maxVersion);
          return (
            <div
              key={version.id}
              role="row"
              className="grid grid-cols-[64px_120px_minmax(0,1fr)_72px_100px_104px_minmax(190px,auto)] items-center gap-3 border-t border-border px-4 py-3 text-sm"
            >
              <span role="cell" className="font-bold text-ink">
                v{version.version}
              </span>
              <span role="cell">
                <StatusPill tone={status.tone}>{status.label}</StatusPill>
              </span>
              <span role="cell" className="min-w-0 truncate text-ink" title={version.title}>
                {version.title}
              </span>
              <span role="cell" className="text-ink-tertiary">
                {version.criteria_count}
              </span>
              <span role="cell" className="text-ink-tertiary">
                {formatDate(version.created_at)}
              </span>
              <span role="cell" className="text-ink-tertiary">
                {version.report_count > 0 ? `${version.report_count} pinned` : "None yet"}
              </span>
              <span role="cell" className="flex items-center justify-end gap-3 whitespace-nowrap">
                <VersionActions
                  version={version}
                  status={status}
                  onActivate={() => handleActivate(version.id, version.version)}
                  onDelete={() => setDeleteTarget(version)}
                  activatePending={activate.isPending}
                  linkClass={desktopActionLinkClass}
                  staticClass="text-sm text-ink-tertiary"
                />
              </span>
            </div>
          );
        })}
        {versions?.length === 0 && (
          <p className="px-4 py-3 text-sm text-ink-tertiary">No versions yet.</p>
        )}
      </div>

      <p className="text-sm text-ink-tertiary">
        A rubric change is a measurement change. Reports stay pinned to the version they were
        graded against so results stay comparable over time.
      </p>

      {uploadOpen && (
        <UploadRubricModal onClose={() => setUploadOpen(false)} familyId={family.rubric_family_id} />
      )}

      {deleteTarget && (
        <DeleteConfirmModal
          target={deleteTarget}
          isPending={del.isPending}
          error={deleteError}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}
