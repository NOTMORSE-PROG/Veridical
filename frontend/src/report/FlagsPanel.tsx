// Flags panel (BUG-033, screen 4h): the report's own list of F4-F7
// integrity flags, each linking to its already-built detail page
// (/flags/:id, FlagDetail.tsx). Before this existed, a real,
// score-changing flag was reachable only by reading raw JSON in the
// audit log — this panel is the fix. Sibling of EscalatedPanel.tsx
// (same left-accent-bar + tone-tinted-header pattern), placed directly
// below it: escalated items are still ungraded (no score exists yet),
// flags are already scored but override-able, the criteria table below
// is fully decided and reference-only.
import { Link, useSearchParams } from "react-router";
import type { FlagSummaryOut } from "../api/types";
import { AnchorPill } from "../components/AnchorPill";
import { cx } from "../components/cx";
import { FirstUploadContextGroupNote } from "../components/FirstUploadContextBanner";
import { SeverityTag, type Severity } from "../components/SeverityTag";
import { StatusPill, type StatusPillTone } from "../components/StatusPill";
import { CHECK_KIND_ORDER, CHECK_KIND_SHORT_LABEL } from "../domain/checkKind";
import { worstFlagTone } from "../domain/flagTone";
import { problemLabel } from "../domain/problemLabel";
import { truncateAtWord } from "../format/text";
import { clusterFlagFindings } from "./flagClusters";
import { useFlags } from "./useReport";

function SpinnerIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="motion-safe:animate-spin motion-reduce:animate-none">
      <path d="M20 12a8 8 0 1 0-2.5 5.8" />
      <path d="M20 8v4h-4" />
    </svg>
  );
}

function FlagIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 3v18" />
      <path d="M5 4h11l-2.5 3.5L16 11H5" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12.5l2.5 2.5L16 9" />
    </svg>
  );
}

// Full literal class names (Tailwind's scanner only sees classes written
// whole — a dynamically composed `bg-status-${tone}-bg` gets dropped
// from the build, cx.ts's own documented constraint).
const TONE_HEADER_BG: Record<StatusPillTone, string> = {
  danger: "bg-status-danger-bg",
  caution: "bg-status-caution-bg",
  info: "bg-status-info-bg",
  // V-069: a flag's severity is never "level" (that tone is only for a
  // levelled criterion's decided RESULT, never a flag) -- present only
  // because `StatusPillTone` is a closed union `Record` requires
  // exhaustively, same info-token pairing as StatusPill.tsx's own choice.
  level: "bg-status-info-bg",
  neutral: "bg-status-neutral-bg",
  success: "bg-status-success-bg",
  attention: "bg-status-attention-bg",
};

const TONE_BORDER_VAR: Partial<Record<StatusPillTone, string>> = {
  danger: "var(--color-status-danger-text)",
  caution: "var(--color-status-caution-text)",
  info: "var(--color-status-info-text)",
  // neutral: no left border — every flag already settled, V-056's
  // "receded, reference-only" visual language (same rule the criteria
  // table below already follows).
};

function severityBreakdown(flags: FlagSummaryOut[]): string {
  const high = flags.filter((f) => f.severity === "high").length;
  const med = flags.filter((f) => f.severity === "med").length;
  const low = flags.filter((f) => f.severity === "low").length;
  const parts: string[] = [];
  if (high) parts.push(`${high} high`);
  if (med) parts.push(`${med} medium`);
  if (low) parts.push(`${low} low`);
  return parts.length ? `${parts.join(", ")} severity` : "";
}

// ux-critic finding (BUG-033 review): this MUST scope to unresolved
// flags only, same as panelSubline() two functions below — an
// already-overridden flag's severity is no longer live risk. Reproduced
// live: a group with one overridden high-severity flag and three
// unresolved low ones showed "1 high, 3 low severity" in its own
// collapsed header, implying an active high-severity issue that didn't
// exist, while the panel's own top-line summary (correctly scoped)
// disagreed with it on the same screen.
// BUG-078 (ux-critic finding, live-reproduced): "overridden" is no longer
// an honest word for every resolved flag here -- a confirmed citation
// source resolved the flag without disagreeing with anything. "Resolved"
// covers both paths truthfully; the per-row pill (FlagRow, above) still
// names the specific one.
function groupCaption(flags: FlagSummaryOut[]): string {
  const unresolved = flags.filter((f) => !f.overridden);
  if (unresolved.length === 0) return "All resolved";
  return severityBreakdown(unresolved);
}

function panelSubline(flags: FlagSummaryOut[]): string {
  const unresolved = flags.filter((f) => !f.overridden);
  const resolved = flags.filter((f) => f.overridden);
  if (unresolved.length > 0 && resolved.length > 0) {
    return `${severityBreakdown(unresolved)}. ${resolved.length} already resolved.`;
  }
  if (unresolved.length > 0) {
    return `${severityBreakdown(unresolved)}.`;
  }
  if (resolved.length === 1) {
    return "This flag was resolved.";
  }
  return `All ${resolved.length} resolved.`;
}

function FlagRow({
  flag,
  showEyebrow,
  linkToDetail,
  hideExcerpt = false,
}: {
  flag: FlagSummaryOut;
  showEyebrow: boolean;
  linkToDetail: boolean;
  // V-071 AC8: set by DuplicateCluster, whose own header already shows
  // the (shared) excerpt and problem label once -- repeating them on
  // every anchor row underneath would reproduce the exact "same sentence
  // N times" wall this AC exists to remove.
  hideExcerpt?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5 border-t border-border px-3.5 py-3 text-sm sm:flex-row sm:items-start sm:justify-between sm:gap-3">
      <div className="min-w-0 flex-1">
        {showEyebrow && (
          <p className="text-xs font-semibold tracking-header text-ink-tertiary uppercase">
            {CHECK_KIND_SHORT_LABEL[flag.check_kind] ?? flag.check_kind}
          </p>
        )}
        {!hideExcerpt && (
          <>
            <p className={flag.overridden ? "text-ink-tertiary" : "text-ink-secondary"}>
              {truncateAtWord(flag.evidence_excerpt, 140)}
            </p>
            {/* V-071 AC9: a short, closed-vocabulary statement of WHICH
                problem this is -- plain text, not a pill (a pill implies
                a state to resolve, same weight as SeverityTag/StatusPill;
                this is a caption). Null for any check_kind/detail.kind
                this project hasn't given honest short copy yet
                (problemLabel.ts), never a guessed label. */}
            {problemLabel(flag.problem_kind) && (
              <p className="mt-0.5 text-xs font-medium text-ink-tertiary">{problemLabel(flag.problem_kind)}</p>
            )}
          </>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <AnchorPill anchor={flag.page_anchor} />
          <SeverityTag severity={flag.severity as Severity} />
          {/* BUG-078 (ux-critic finding, live-reproduced): a confirmed
              citation source is not a disagreement with anything -- the
              same "Overridden" pill this row showed for both would
              conflate exactly what FlagDetail.tsx's own terminal banner
              is careful never to conflate. */}
          {flag.confirmed_citation_source ? (
            <StatusPill tone="success">Source confirmed</StatusPill>
          ) : (
            flag.overridden && <StatusPill tone="neutral">Overridden</StatusPill>
          )}
        </div>
      </div>
      {/* V-040: the adviser view has nowhere for this to go -- /flags/:id
          is RequireAuth-wrapped, and FlagSummaryOut already carries
          everything this audience needs inline (excerpt/anchor/severity;
          its own docstring's "enough to decide," never "enough to skip
          clicking," is exactly the bar this reduced view meets). Omitting
          the link entirely is honest; pointing it at a route that would
          just bounce the reader to a sign-in wall is not. */}
      {linkToDetail && (
        <Link
          to={`/flags/${flag.id}`}
          className="flex min-h-11 flex-none items-center rounded-md border border-border-input bg-panel px-3 text-sm font-medium text-ink hover:bg-status-neutral-bg sm:min-h-9"
        >
          {flag.overridden ? "View details" : "Review evidence"}
        </Link>
      )}
    </div>
  );
}

// V-071 AC8 / BUG-141: delegate identity decisions to the shared helper
// used by the routed report. Reuse findings require the same problem and
// archived-manuscript reference; other checks require the same problem,
// criterion, and exact excerpt. Missing identity stays a singleton. That
// keeps two different problems from disappearing into one card while still
// collapsing per-page locations of one finding. The helper preserves first
// occurrence order, so it does not fight the backend's fixed
// severity/resolution ordering (test_report_flags_live.py).
function clusterFlags(flags: FlagSummaryOut[]): FlagSummaryOut[][] {
  return clusterFlagFindings(flags).map((cluster) => cluster.flags);
}

// `ux-critic` finding (V-071 AC8 review, live-reproduced): keying a
// cluster on `cluster[0].id` broke the moment the instructor resolved
// one of its members -- the backend re-sorts unresolved-before-overridden
// (test_report_flags_live.py's own fixed ordering), which moves a
// resolved flag to the end of the group and changes which flag is
// "first" in `clusterFlags`'s order-preserving output. The URL's
// `flags_clusters_open=<old first id>` then matched nothing on the next
// load, silently re-collapsing a cluster the instructor had just acted
// on. The min member id is stable across that reorder (ids never change,
// only array position does), so it survives the exact transition that
// broke the old key.
function clusterKey(flags: FlagSummaryOut[]): number {
  return Math.min(...flags.map((f) => f.id));
}

function worstSeverity(flags: FlagSummaryOut[]): Severity {
  const rank: Record<Severity, number> = { high: 3, med: 2, low: 1 };
  return flags.reduce<Severity>(
    (worst, f) => (rank[f.severity as Severity] > rank[worst] ? (f.severity as Severity) : worst),
    flags[0].severity as Severity,
  );
}

// A resolved anchor within a duplicate cluster is genuinely per-anchor
// (each duplicate is its own `Flag` row with its own `overridden`) --
// unlike the parent group's all-or-nothing "All resolved" caption, a
// cluster can be partly resolved, and the instructor needs to see that
// to know how many of the N anchors still need attention.
function clusterResolutionPill(flags: FlagSummaryOut[]) {
  const resolved = flags.filter((f) => f.overridden).length;
  if (resolved === 0) return null;
  return (
    <StatusPill tone="neutral">
      {resolved === flags.length ? `All ${flags.length} resolved` : `${resolved} of ${flags.length} resolved`}
    </StatusPill>
  );
}

function DuplicateCluster({
  flags,
  open,
  onToggle,
  linkToDetail,
}: {
  flags: FlagSummaryOut[];
  open: boolean;
  onToggle: () => void;
  linkToDetail: boolean;
}) {
  const first = flags[0];
  const listId = `cluster-${clusterKey(flags)}-list`;
  const mixedSeverity = new Set(flags.map((f) => f.severity)).size > 1;
  return (
    <div className="border-t border-border">
      <div className="flex flex-col gap-1.5 px-3.5 py-3 text-sm">
        <p className={flags.every((f) => f.overridden) ? "text-ink-tertiary" : "text-ink-secondary"}>
          {truncateAtWord(first.evidence_excerpt, 140)}
        </p>
        {problemLabel(first.problem_kind) && (
          <p className="text-xs font-medium text-ink-tertiary">{problemLabel(first.problem_kind)}</p>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={listId}
            onClick={onToggle}
            className="flex min-h-11 items-center rounded-full bg-status-neutral-bg px-2 py-0.5 text-xs font-medium text-ink-tertiary sm:min-h-9"
          >
            {flags.length} locations
          </button>
          <SeverityTag severity={worstSeverity(flags)} />
          {mixedSeverity && <span className="text-xs text-ink-tertiary">{severityBreakdown(flags)}</span>}
          {clusterResolutionPill(flags)}
        </div>
      </div>
      {open && (
        <div id={listId}>
          {flags.map((flag) => (
            <FlagRow key={flag.id} flag={flag} showEyebrow={false} linkToDetail={linkToDetail} hideExcerpt />
          ))}
        </div>
      )}
    </div>
  );
}

function FlagGroup({
  kind,
  flags,
  open,
  onToggle,
  linkToDetail,
  clustersOpen,
  onToggleCluster,
}: {
  kind: string;
  flags: FlagSummaryOut[];
  open: boolean;
  onToggle: () => void;
  linkToDetail: boolean;
  clustersOpen: Set<string>;
  onToggleCluster: (firstFlagId: number) => void;
}) {
  if (flags.length === 1) {
    return <FlagRow flag={flags[0]} showEyebrow linkToDetail={linkToDetail} />;
  }
  const clusters = clusterFlags(flags);
  return (
    <div className="border-t border-border">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex min-h-11 w-full items-center justify-between gap-2 px-3.5 py-2.5 text-left sm:min-h-9"
      >
        <span className="flex items-center gap-2">
          <span aria-hidden="true" className="flex-none text-ink-tertiary">
            {open ? "−" : "+"}
          </span>
          <span className="text-sm font-semibold text-ink">
            {CHECK_KIND_SHORT_LABEL[kind] ?? kind} ({flags.length})
          </span>
        </span>
        <span className="text-xs text-ink-tertiary">{groupCaption(flags)}</span>
      </button>
      {open &&
        clusters.map((cluster) =>
          cluster.length > 1 ? (
            <DuplicateCluster
              key={clusterKey(cluster)}
              flags={cluster}
              open={clustersOpen.has(String(clusterKey(cluster)))}
              onToggle={() => onToggleCluster(clusterKey(cluster))}
              linkToDetail={linkToDetail}
            />
          ) : (
            <FlagRow key={cluster[0].id} flag={cluster[0]} showEyebrow={false} linkToDetail={linkToDetail} />
          ),
        )}
    </div>
  );
}

// Comma-separated check_kinds the instructor has manually toggled away
// from their computed default — a URL search param, not component
// state (ux-critic finding, BUG-033 review): `FlagsPanel` remounts on
// every route change, so `useState` here lost the instructor's manual
// collapse/expand the moment they opened one flag's evidence and hit
// Back — reproduced live, forced re-doing triage on every single flag
// opened at 17-flag density. Same pattern AuditLog.tsx already
// established for its own filter/detail state surviving navigation.
const TOGGLED_PARAM = "flags_toggled";

function readToggled(searchParams: URLSearchParams): Set<string> {
  const raw = searchParams.get(TOGGLED_PARAM);
  return raw ? new Set(raw.split(",")) : new Set();
}

// V-071 AC8: which duplicate clusters the instructor has manually opened
// -- same URL-param-not-component-state rationale as TOGGLED_PARAM above
// (survives the FlagsPanel remount a Back navigation causes). Keyed by
// `clusterKey` (the cluster's min member id), not by array position or
// index -- `ux-critic` reproduced live that keying on "first flag in the
// CURRENT order" broke the instant one member was resolved (the backend
// re-sorts unresolved-before-overridden, changing which flag is first),
// silently re-collapsing a cluster the instructor had just acted on. The
// min id is stable across that reorder. Default is closed for every
// cluster regardless of resolved/unresolved mix
// (unlike a check_kind group's own default-open-if-unresolved rule): the
// group header already surfaces the once-only excerpt, problem label,
// severity, and resolution count -- FlagSummaryOut's own "enough to
// decide whether to click" bar -- so auto-opening every cluster too would
// reproduce the exact wall of repeated sentences this AC removes.
const CLUSTERS_OPEN_PARAM = "flags_clusters_open";

function readClustersOpen(searchParams: URLSearchParams): Set<string> {
  const raw = searchParams.get(CLUSTERS_OPEN_PARAM);
  return raw ? new Set(raw.split(",")) : new Set();
}

/** The report's flags list, presentational -- takes an already-fetched
 * `flags` array directly (BUG-033, extended V-040 so the public adviser
 * view can render the SAME list from its own already-fetched
 * `SharedReportOut.flags`, no second network call). `linkToDetail`
 * defaults true (the instructor's own screen, via `FlagsPanel` below);
 * the adviser view passes `false` -- `/flags/:id` is auth-gated and
 * would just bounce a logged-out reader. */
export function FlagsList({
  flags,
  linkToDetail = true,
}: {
  flags: FlagSummaryOut[];
  linkToDetail?: boolean;
}) {
  // Default open state is computed per group (any unresolved flag =>
  // open — ui-designer spec: trust over scanability where the two
  // conflict, since a "merely low-severity" group can still be what's
  // driving Not Ready). This set holds only kinds the instructor has
  // manually toggled AWAY from that default; membership flips the
  // group's open state rather than setting it directly.
  const [searchParams, setSearchParams] = useSearchParams();
  const toggledAwayFromDefault = readToggled(searchParams);
  const clustersOpen = readClustersOpen(searchParams);

  if (flags.length === 0) {
    return (
      <p className="flex items-center gap-2 rounded-lg bg-status-success-bg px-4 py-2.5 text-sm text-status-success-text">
        <CheckIcon />
        No integrity flags on this run. VERIDICAL's four integrity checks (internal agreement,
        citation integrity, statistical forensics, and originality/reuse) found nothing to report.
      </p>
    );
  }

  const tone = worstFlagTone(flags);
  const groups: { kind: string; flags: FlagSummaryOut[] }[] = CHECK_KIND_ORDER.map((kind) => ({
    kind,
    flags: flags.filter((f) => f.check_kind === kind),
  })).filter((g) => g.flags.length > 0);
  // Any check_kind outside the known F4-F7 set (shouldn't happen, but an
  // honest catch-all beats silently dropping a real flag) renders last,
  // grouped by its own kind.
  const knownKinds = new Set<string>(CHECK_KIND_ORDER);
  const otherKinds = [...new Set(flags.filter((f) => !knownKinds.has(f.check_kind)).map((f) => f.check_kind))];
  for (const kind of otherKinds) {
    groups.push({ kind, flags: flags.filter((f) => f.check_kind === kind) });
  }

  function toggle(kind: string) {
    const toggled = readToggled(searchParams);
    if (toggled.has(kind)) toggled.delete(kind);
    else toggled.add(kind);
    const next = new URLSearchParams(searchParams);
    if (toggled.size > 0) next.set(TOGGLED_PARAM, [...toggled].join(","));
    else next.delete(TOGGLED_PARAM);
    // `replace`, not push (unlike AuditLog.tsx's own setParam): a toggle
    // isn't a navigation the instructor would expect Back to undo one
    // click at a time — it should just reflect their LATEST arrangement
    // when they return from a flag's detail page.
    setSearchParams(next, { replace: true });
  }

  function toggleCluster(firstFlagId: number) {
    const open = readClustersOpen(searchParams);
    const key = String(firstFlagId);
    if (open.has(key)) open.delete(key);
    else open.add(key);
    const next = new URLSearchParams(searchParams);
    if (open.size > 0) next.set(CLUSTERS_OPEN_PARAM, [...open].join(","));
    else next.delete(CLUSTERS_OPEN_PARAM);
    setSearchParams(next, { replace: true });
  }

  return (
    <section
      aria-labelledby="flags-heading"
      className="overflow-hidden rounded-lg bg-panel"
      style={{ borderLeftWidth: TONE_BORDER_VAR[tone] ? "4px" : undefined, borderLeftColor: TONE_BORDER_VAR[tone] }}
    >
      <div className={cx("flex flex-wrap items-start gap-2 px-3.5 py-2.5", TONE_HEADER_BG[tone])}>
        <FlagIcon />
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h2 id="flags-heading" tabIndex={-1} className="scroll-mt-16 text-sm font-bold text-ink">
            Flags ({flags.length})
          </h2>
          <p className="text-xs text-ink-tertiary">{panelSubline(flags)}</p>
        </div>
        <span className="text-xs text-ink-tertiary">
          Possible inconsistencies found by VERIDICAL's integrity checks (F4 to F7), not accusations.
        </span>
      </div>
      {groups.flatMap(({ kind, flags: groupFlags }) => {
        const hasUnresolved = groupFlags.some((f) => !f.overridden);
        const open = toggledAwayFromDefault.has(kind) ? !hasUnresolved : hasUnresolved;
        // BUG-097: first_upload is computed once per check run and applied
        // uniformly to every originality/reuse match it produces, so any
        // flag in this group carrying it means the whole group does.
        const showFirstUploadNote =
          kind === "originality_reuse" && groupFlags.some((f) => f.first_upload_context);
        return [
          showFirstUploadNote && <FirstUploadContextGroupNote key={`${kind}-first-upload`} />,
          <FlagGroup
            key={kind}
            kind={kind}
            flags={groupFlags}
            open={open}
            onToggle={() => toggle(kind)}
            linkToDetail={linkToDetail}
            clustersOpen={clustersOpen}
            onToggleCluster={toggleCluster}
          />,
        ];
      })}
    </section>
  );
}

export function FlagsPanel({ checkRunId }: { checkRunId: number }) {
  const { data: flags, isPending, isError, refetch } = useFlags(checkRunId);

  if (isPending) {
    return (
      <div role="status" aria-live="polite" aria-busy="true" className="flex items-center gap-2 rounded-lg border border-border bg-panel p-4 text-sm text-ink-secondary">
        <SpinnerIcon />
        Loading flags.
      </div>
    );
  }
  if (isError || !flags) {
    return (
      <div role="alert" className="rounded-lg border border-status-attention-text/25 bg-status-attention-bg p-4 text-sm text-status-attention-text">
        This report's flags couldn't be loaded.{" "}
        <button type="button" onClick={() => refetch()} className="font-medium underline">
          Try again
        </button>
        .
      </div>
    );
  }
  return <FlagsList flags={flags} />;
}
