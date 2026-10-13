import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import { useLibraryRouteFocus } from "./useLibraryRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { ApiError } from "../api/client";
import type { LibraryItemOut } from "../api/types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { LIBRARY_TABS_MEDIA_QUERY } from "../config/ui";
import { useMediaQuery } from "../ui/useMediaQuery";
import { Dialog } from "../ui/Dialog";
import { LibraryContentPane, type LibraryDocumentState } from "./LibraryContentPane";
import { libraryRecordName } from "./libraryFormat";
import { useDecideLibraryEntry, useLibraryDocument, useLibraryItem } from "./useLibrary";

function ownershipNote(aOwn: boolean, bOwn: boolean): string {
  if (aOwn && bOwn) return "Both records belong to your account. Read their stored manuscripts below.";
  return "You can read both manuscripts in full. Identifying record fields from other instructors are hidden; the files may contain those details.";
}

function ComparisonPane({
  manuscriptId, active, tabId, panelId, tabbed,
}: { manuscriptId: number; active: boolean; tabId: string; panelId: string; tabbed: boolean }) {
  const { data: item, isPending, isError, refetch } = useLibraryItem(manuscriptId);
  const documentQuery = useLibraryDocument(
    manuscriptId,
    Boolean(item && (item.status === "active" || item.status === "needs_decision")),
  );
  const documentState: LibraryDocumentState = {
    viewer: documentQuery.data,
    isPending: documentQuery.isPending,
    isError: documentQuery.isError,
    removedAt: null,
    onRetry: () => void documentQuery.refetch(),
  };

  const panelProps = {
    id: panelId,
    role: tabbed ? "tabpanel" : "region",
    "aria-labelledby": tabbed ? tabId : undefined,
    "aria-label": tabbed ? undefined : "Manuscript " + manuscriptId,
  };
  if (isPending) return (
    <section {...panelProps} className="signal-library-compare-pane signal-library-state" data-active={active}>
      <p role="status" aria-live="polite">Loading comparison record.</p>
    </section>
  );
  if (isError || !item) {
    return (
      <section {...panelProps} className="signal-library-compare-pane signal-library-state" data-active={active}>
        <strong role="alert">This comparison record could not be loaded.</strong>
        <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
      </section>
    );
  }

  return (
    <section
      {...panelProps}
      className="signal-library-compare-pane"
      data-active={active}
    >
      <header>
        <span className="signal-library-ownership">
          {item.is_own ? "Your record" : "Another instructor's record"}
        </span>
        <h2>{libraryRecordName(item)}</h2>
        {item.is_own && item.team_name && <p>{item.team_name}</p>}
      </header>
      <div className="signal-library-compare-pane__source">
        <LibraryContentPane
          manuscriptId={manuscriptId}
          documentState={documentState}
        />
      </div>
    </section>
  );
}

/** V-077 (first-time-user review pass): when one side is the instructor's own upload waiting
 * for a duplicate decision, the decision sits beside the evidence. */
function DecisionBar({ waiting }: { waiting: LibraryItemOut }) {
  const navigate = useNavigate();
  const decide = useDecideLibraryEntry();
  const [confirmingSkip, setConfirmingSkip] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileName = waiting.original_filename ?? libraryRecordName(waiting);

  async function run(action: "add" | "skip") {
    setError(null);
    try {
      await decide.mutateAsync({ manuscriptId: waiting.manuscript_id, action });
      if (action === "add") navigate(`/library/${waiting.manuscript_id}`);
      else navigate("/library", { state: { skippedTitle: fileName } });
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "The decision could not be saved. Try again.");
    }
  }

  return (
    <Alert tone="warning" title={`Decide about your upload “${fileName}”`}>
      <p>Its text is very similar to your other record shown here. It is not in the Library until you decide.</p>
      <div className="signal-control-cluster">
        <Button type="button" variant="secondary" busy={decide.isPending} onClick={() => void run("add")}>
          Add anyway
        </Button>
        <Button type="button" variant="secondary" disabled={decide.isPending} onClick={() => setConfirmingSkip(true)}>
          Skip this copy
        </Button>
        <ActionLink to={`/library/${waiting.manuscript_id}`} variant="quiet">Back to the upload</ActionLink>
      </div>
      {error && <p role="alert">{error}</p>}
      {confirmingSkip && (
        <Dialog
          title="Skip this copy?"
          onClose={() => setConfirmingSkip(false)}
          actions={
            <>
              <Button type="button" variant="secondary" onClick={() => setConfirmingSkip(false)}>Keep deciding</Button>
              <Button
                type="button"
                variant="danger"
                onClick={() => {
                  setConfirmingSkip(false);
                  void run("skip");
                }}
              >
                Skip and delete this copy
              </Button>
            </>
          }
        >
          <p>The uploaded file “{fileName}” will not be added and will not be kept. Your other record is not affected.</p>
        </Dialog>
      )}
    </Alert>
  );
}

export function SignalLibraryComparePage() {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const parsedA = Number(searchParams.get("a"));
  const parsedB = Number(searchParams.get("b"));
  const a = Number.isInteger(parsedA) && parsedA > 0 ? parsedA : undefined;
  const b = Number.isInteger(parsedB) && parsedB > 0 ? parsedB : undefined;
  const collectionSearch = (location.state as { collectionSearch?: string } | null)?.collectionSearch ?? "";
  const collectionRoute = { pathname: "/library", search: collectionSearch };
  const collectionState = { comparisonSelection: a && b ? [a, b] : [] };
  const [active, setActive] = useState<"a" | "b">("a");
  const tabsId = useId();
  const tabbed = useMediaQuery(LIBRARY_TABS_MEDIA_QUERY, true);
  function moveTab(event: KeyboardEvent<HTMLButtonElement>, current: "a" | "b") {
    let next: "a" | "b";
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") next = current === "a" ? "b" : "a";
    else if (event.key === "Home") next = "a";
    else if (event.key === "End") next = "b";
    else return;
    event.preventDefault();
    document.getElementById(tabsId + "-tab-" + next)?.focus();
  }
  const headingRef = useRef<HTMLHeadingElement>(null);
  useLibraryRouteFocus("Compare records - VERIDICAL", headingRef);
  const itemA = useLibraryItem(a);
  const itemB = useLibraryItem(b);

  return (
    <div className="signal-route signal-page-flow signal-library-compare">
      <header className="signal-route-header signal-library-compare__header">
        <div>
          <nav className="signal-breadcrumb" aria-label="Breadcrumb">
            <Link to={collectionRoute} state={collectionState}>Manuscript Library</Link>
            <span aria-hidden="true">/</span>
            <span>Compare</span>
          </nav>
          <p className="signal-eyebrow">Side-by-side evidence reading</p>
          <h1 ref={headingRef} tabIndex={-1}>Compare records</h1>
          <p className="signal-route-header__intro">
            Read two records side by side. This view does not compare them for you; you decide what they have in
            common.
          </p>
        </div>
        <ActionLink to={collectionRoute} state={collectionState} variant="secondary">Change selection</ActionLink>
      </header>

      {(!a || !b) && (
        <section className="signal-library-state" role="alert">
          <h2>Two valid records are required</h2>
          <p>Return to the Library, choose Compare two records, and select two records.</p>
          <ActionLink to="/library" variant="brand">Choose records</ActionLink>
        </section>
      )}

      {a && b && (
        <div className="signal-section-flow signal-library-compare-workspace">
          {(() => {
            const waiting = [itemA.data, itemB.data].find(
              (item) => item?.is_own && item.status === "needs_decision",
            );
            return waiting ? <DecisionBar waiting={waiting} /> : null;
          })()}
          {itemA.data && itemB.data && (
            <aside className="signal-library-privacy">
              <strong>Visibility for this comparison</strong>
              <p>{ownershipNote(itemA.data.is_own, itemB.data.is_own)}</p>
            </aside>
          )}
          {tabbed && (
            <div className="signal-library-tabs" role="tablist" aria-label="Comparison manuscript">
              {(["a", "b"] as const).map((key) => (
                <button
                  key={key}
                  id={tabsId + "-tab-" + key}
                  type="button"
                  role="tab"
                  aria-selected={active === key}
                  aria-controls={tabsId + "-panel-" + key}
                  tabIndex={active === key ? 0 : -1}
                  onClick={() => setActive(key)}
                  onKeyDown={(event) => moveTab(event, key)}
                >
                  {key === "a" ? "First record" : "Second record"}
                </button>
              ))}
            </div>
          )}
          <div className="signal-library-compare-grid">
            <ComparisonPane manuscriptId={a} active={active === "a"} tabId={tabsId + "-tab-a"} panelId={tabsId + "-panel-a"} tabbed={tabbed} />
            <ComparisonPane manuscriptId={b} active={active === "b"} tabId={tabsId + "-tab-b"} panelId={tabsId + "-panel-b"} tabbed={tabbed} />
          </div>
        </div>
      )}
    </div>
  );
}
