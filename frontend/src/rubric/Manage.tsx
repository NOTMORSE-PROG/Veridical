// V-073 screen 4m — Rubric Studio. The active format is the workspace;
// versions form an explicit timeline and never look like interchangeable rows.
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import type { RubricListItem } from "../api/types";
import { usePrograms } from "../dashboard/useDashboard";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { RerunModal } from "./RerunModal";
import { UploadRubricModal } from "./UploadRubricModal";
import {
  useActivateRubric,
  useDeleteRubric,
  useRubricFamilies,
  useRubricVersions,
  useSetRubricFamilyProgram,
} from "./useRubric";

interface DeleteTarget {
  id: number;
  version: number;
  title: string;
}

function formatDate(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(iso));
}

function ProgramControl({ familyId, program }: { familyId: string; program: string | null }) {
  const { data: programs, isPending } = usePrograms();
  const updateProgram = useSetRubricFamilyProgram();
  const [error, setError] = useState<string>();
  const fieldId = useId();
  if (isPending || !programs?.length) return null;
  const currentId = program ? programs.find((item) => item.name === program)?.id : undefined;

  async function onChange(value: string) {
    setError(undefined);
    try {
      await updateProgram.mutateAsync({
        familyId,
        programId: value ? Number(value) : null,
      });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not update the program. Try again.");
    }
  }

  return (
    <div className="signal-studio-field">
      <label htmlFor={fieldId}>Program</label>
      <select
        id={fieldId}
        value={currentId ?? ""}
        disabled={updateProgram.isPending}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Not set</option>
        {programs.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

function VersionState({ version, latest }: { version: RubricListItem; latest: number }) {
  const label = version.is_active ? "Active" : version.version === latest ? "Draft" : "Superseded";
  return <span className={`signal-version-state signal-version-state--${label.toLowerCase()}`}>{label}</span>;
}

export function ManageRubricPage() {
  const headingRef = useRef<HTMLHeadingElement>(null);
  const historyHeadingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Rubric Studio - VERIDICAL", headingRef);

  const { data: families, isPending, isError, refetch } = useRubricFamilies();
  const defaultFamily = (families ?? []).find((item) => item.is_active) ?? families?.[0];
  const [selectedFamilyId, setSelectedFamilyId] = useState<string>();
  const family = (families ?? []).find((item) => item.rubric_family_id === selectedFamilyId)
    ?? defaultFamily;
  const { data: versions } = useRubricVersions(family?.rubric_family_id);
  const activate = useActivateRubric();
  const remove = useDeleteRubric();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [rerunOpen, setRerunOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget>();
  const [deleteError, setDeleteError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const [liveMessage, setLiveMessage] = useState("");
  const [recentActivation, setRecentActivation] = useState<number>();

  useEffect(() => {
    if (selectedFamilyId || !defaultFamily) return;
    setSelectedFamilyId(defaultFamily.rubric_family_id);
  }, [defaultFamily, selectedFamilyId]);

  async function activateVersion(version: RubricListItem) {
    setActionError(undefined);
    try {
      await activate.mutateAsync(version.id);
      setLiveMessage(`Version ${version.version} is now active.`);
      setRecentActivation(version.version);
      historyHeadingRef.current?.focus();
    } catch (cause) {
      setActionError(cause instanceof ApiError ? cause.message : "Could not activate this version. Try again.");
    }
  }

  async function deleteVersion() {
    if (!deleteTarget) return;
    setDeleteError(undefined);
    try {
      await remove.mutateAsync(deleteTarget.id);
      setLiveMessage(`Version ${deleteTarget.version} deleted.`);
      setDeleteTarget(undefined);
      historyHeadingRef.current?.focus();
    } catch (cause) {
      setDeleteError(cause instanceof ApiError ? cause.message : "Could not delete this version. Try again.");
    }
  }

  if (isPending) {
    return (
      <div className="signal-route signal-page-flow signal-rubric-studio">
        <header className="signal-route-header"><div><p className="signal-eyebrow">Prepare</p><h1 ref={headingRef} tabIndex={-1}>Rubric Studio</h1></div></header>
        <div className="signal-desk-loading" role="status" aria-busy="true"><span>Loading required formats…</span><i /><i /></div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="signal-route signal-page-flow signal-rubric-studio">
        <header className="signal-route-header"><div><p className="signal-eyebrow">Prepare</p><h1 ref={headingRef} tabIndex={-1}>Rubric Studio</h1></div></header>
        <Alert title="Could not load required formats" tone="error" role="alert">
          <p>Your formats have not changed.</p><Button variant="secondary" onClick={() => refetch()}>Try again</Button>
        </Alert>
      </div>
    );
  }

  if (!family) {
    return (
      <div className="signal-route signal-page-flow signal-rubric-studio">
        <header className="signal-route-header">
          <div><p className="signal-eyebrow">Prepare</p><h1 ref={headingRef} tabIndex={-1}>Rubric Studio</h1><p className="signal-route-header__intro">Prepare the criteria VERIDICAL will use. You review every criterion before any manuscript check.</p></div>
        </header>
        <section className="signal-first-use" aria-labelledby="no-format-heading">
          <p className="signal-eyebrow">Required format</p><h2 id="no-format-heading">No required format yet</h2>
          <p>Upload a PDF or DOCX. VERIDICAL prepares a draft set of criteria for your review.</p>
          <Button variant="brand" onClick={() => setUploadOpen(true)}>Upload required format</Button>
        </section>
        {uploadOpen && <UploadRubricModal onClose={() => setUploadOpen(false)} />}
      </div>
    );
  }

  const orderedVersions = [...(versions ?? [family])].sort((a, b) => b.version - a.version);
  const latestVersion = orderedVersions[0]?.version ?? family.version;
  const activeVersion = orderedVersions.find((item) => item.is_active);
  const displayVersion = activeVersion ?? orderedVersions[0] ?? family;

  return (
    <div className="signal-route signal-page-flow signal-rubric-studio">
      <header className="signal-route-header">
        <div>
          <p className="signal-eyebrow">Prepare</p>
          <h1 ref={headingRef} tabIndex={-1}>Rubric Studio</h1>
          <p className="signal-route-header__intro">Manage the required format, review prepared criteria, and keep report-producing versions traceable.</p>
        </div>
        <div className="signal-route-actions"><Button variant="brand" onClick={() => setUploadOpen(true)}>Upload new format</Button></div>
      </header>

      <p className="signal-live-region" role="status" aria-live="polite">{liveMessage}</p>
      {actionError && <Alert title="Could not update this format" tone="error" role="alert">{actionError}</Alert>}

      {(families?.length ?? 0) > 1 && (
        <div className="signal-family-switcher">
          <label htmlFor="required-format-family">Required format</label>
          <select
            id="required-format-family"
            value={family.rubric_family_id}
            onChange={(event) => setSelectedFamilyId(event.target.value)}
          >
            {families?.map((item) => <option key={item.rubric_family_id} value={item.rubric_family_id}>{item.title}</option>)}
          </select>
          <p>{families?.length} format families are available. Reports remain pinned to the version that produced them.</p>
        </div>
      )}

      <section className="signal-active-format" aria-labelledby="active-format-heading">
        <div className="signal-active-format__rail" aria-hidden="true"><span /></div>
        <div>
          <p className="signal-eyebrow">{activeVersion ? "Active format" : "Latest draft"}</p>
          <h2 id="active-format-heading">{displayVersion.title}</h2>
          <div className="signal-active-format__facts">
            <span>Version {displayVersion.version}</span>
            <span>{displayVersion.criteria_count} {displayVersion.criteria_count === 1 ? "criterion" : "criteria"}</span>
            <VersionState version={displayVersion} latest={latestVersion} />
          </div>
          <ProgramControl familyId={family.rubric_family_id} program={displayVersion.program} />
          {!activeVersion && <Alert title="No active version" tone="warning">Review and activate a version before starting manuscript checks.</Alert>}
        </div>
        <div className="signal-active-format__actions">
          <ActionLink to={`/rubric/${displayVersion.id}/review`} variant={activeVersion ? "secondary" : "brand"}>
            {activeVersion ? "View criteria" : "Review and activate"}
          </ActionLink>
          {activeVersion && <Button variant="secondary" onClick={() => setRerunOpen(true)}>Re-run manuscripts</Button>}
        </div>
      </section>

      {recentActivation !== undefined && (
        <Alert title={`Version ${recentActivation} is now active`} tone="success" role="status">
          <p>Existing reports stay with their original version. You can choose which manuscripts to run again.</p>
          <Button variant="secondary" onClick={() => { setRecentActivation(undefined); setRerunOpen(true); }}>Choose manuscripts</Button>
        </Alert>
      )}

      <section className="signal-version-history" aria-labelledby="version-history-heading">
        <div className="signal-section-heading">
          <div><p className="signal-eyebrow">Traceability</p><h2 ref={historyHeadingRef} tabIndex={-1} id="version-history-heading">Version history</h2></div>
          <p>Newest first. Used versions cannot be rewritten.</p>
        </div>
        <ol>
          {orderedVersions.map((version) => (
            <li key={version.id}>
              <span className="signal-version-history__node" aria-hidden="true" />
              <div className="signal-version-history__summary">
                <p><strong>v{version.version}</strong><span>{version.title}</span></p>
                <span>{formatDate(version.created_at)}</span>
              </div>
              <div className="signal-version-history__facts">
                <VersionState version={version} latest={latestVersion} />
                <span>{version.criteria_count} criteria</span>
                <span>{version.report_count} pinned {version.report_count === 1 ? "report" : "reports"}</span>
              </div>
              <div className="signal-version-history__actions">
                <Link to={`/rubric/${version.id}/review`}>{version.is_active ? "View" : "Review"}</Link>
                {!version.is_active && <button type="button" disabled={activate.isPending} onClick={() => activateVersion(version)}>Activate</button>}
                {!version.is_active && (version.report_count > 0
                  ? <span>Reports pinned</span>
                  : <button type="button" onClick={() => setDeleteTarget({ id: version.id, version: version.version, title: version.title })}>Delete</button>)}
              </div>
            </li>
          ))}
        </ol>
      </section>

      {deleteTarget && (
        <Dialog
          title={`Delete version ${deleteTarget.version}?`}
          onClose={remove.isPending ? undefined : () => setDeleteTarget(undefined)}
          actions={<><Button variant="secondary" disabled={remove.isPending} onClick={() => setDeleteTarget(undefined)}>Cancel</Button><Button variant="danger" busy={remove.isPending} onClick={deleteVersion}>{remove.isPending ? "Deleting" : "Delete version"}</Button></>}
        >
          <div className="signal-dialog-copy" aria-busy={remove.isPending}>
            <p>You are about to permanently delete <strong>{deleteTarget.title}</strong>, version {deleteTarget.version}. This cannot be undone.</p>
            <p>It has no reports pinned to it, so existing readiness reports will not change.</p>
            {remove.isPending && <p role="status">Deleting version {deleteTarget.version}.</p>}
            {deleteError && <Alert title="Could not delete this version" tone="error" role="alert">{deleteError}</Alert>}
          </div>
        </Dialog>
      )}
      {uploadOpen && <UploadRubricModal onClose={() => setUploadOpen(false)} />}
      {rerunOpen && <RerunModal onClose={() => setRerunOpen(false)} />}
    </div>
  );
}
