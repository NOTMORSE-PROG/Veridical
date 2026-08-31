import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { ActionLink } from "../ui/ActionLink";
import { Button } from "../ui/Button";
import { LibraryContentPane, type OwnDocumentState } from "./LibraryContentPane";
import { useLibraryDocument, useLibraryItem } from "./useLibrary";

function ownershipNote(aOwn: boolean, bOwn: boolean): string {
  if (aOwn && bOwn) return "Both records belong to your account. Their stored sources appear when they can be opened.";
  if (!aOwn && !bOwn) return "Both records belong to other instructors. Each side is limited to bounded chapter excerpts.";
  return "One record belongs to your account and may show its stored source. The other instructor's record is limited to bounded chapter excerpts.";
}

function ComparisonPane({ manuscriptId, active }: { manuscriptId: number; active: boolean }) {
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

  if (isPending) return <section className="signal-library-compare-pane signal-library-state" data-active={active} role="status" aria-live="polite">Loading comparison record.</section>;
  if (isError || !item) {
    return (
      <section className="signal-library-compare-pane signal-library-state" data-active={active} role="alert">
        <strong>This comparison record could not be loaded.</strong>
        <Button type="button" variant="secondary" onClick={() => refetch()}>Try again</Button>
      </section>
    );
  }

  const identity = item.title
    ? { primary: item.title, secondary: item.group_label }
    : manuscriptIdentity(item.group_label, item.original_filename);

  return (
    <section className="signal-library-compare-pane" data-active={active}>
      <header>
        <span className="signal-library-badge" data-tone={item.is_own ? "own" : "shared"}>
          {item.is_own ? "Your manuscript" : "Bounded shared excerpt"}
        </span>
        <h2>{identity.primary}</h2>
        {identity.secondary && <p>{identity.secondary}</p>}
      </header>
      <div className="signal-library-compare-pane__source">
        <LibraryContentPane
          manuscriptId={manuscriptId}
          isOwn={item.is_own}
          ownDocument={item.is_own ? ownDocument : undefined}
        />
      </div>
    </section>
  );
}

export function SignalLibraryComparePage() {
  const [searchParams] = useSearchParams();
  const parsedA = Number(searchParams.get("a"));
  const parsedB = Number(searchParams.get("b"));
  const a = Number.isInteger(parsedA) && parsedA > 0 ? parsedA : undefined;
  const b = Number.isInteger(parsedB) && parsedB > 0 ? parsedB : undefined;
  const [active, setActive] = useState<"a" | "b">("a");
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Compare manuscripts - VERIDICAL", headingRef);
  const itemA = useLibraryItem(a);
  const itemB = useLibraryItem(b);

  return (
    <div className="signal-route signal-library-compare">
      <header className="signal-route-header signal-library-compare__header">
        <div>
          <nav className="signal-breadcrumb" aria-label="Breadcrumb">
            <Link to="/library">Manuscript Library</Link>
            <span aria-hidden="true">/</span>
            <span>Compare</span>
          </nav>
          <p className="signal-eyebrow">Side-by-side evidence reading</p>
          <h1 ref={headingRef} tabIndex={-1}>Compare manuscripts</h1>
          <p className="signal-route-header__intro">
            Read two stored records together. This view does not produce a new similarity judgment.
          </p>
        </div>
        <ActionLink to="/library" variant="secondary">Change selection</ActionLink>
      </header>

      {(!a || !b) && (
        <section className="signal-library-state" role="alert">
          <h2>Two valid records are required</h2>
          <p>Return to the Library, enter comparison mode, and select two manuscripts.</p>
          <ActionLink to="/library" variant="brand">Choose records</ActionLink>
        </section>
      )}

      {a && b && (
        <>
          {itemA.data && itemB.data && (
            <aside className="signal-library-privacy">
              <strong>Visibility for this comparison</strong>
              <p>{ownershipNote(itemA.data.is_own, itemB.data.is_own)}</p>
            </aside>
          )}
          <div className="signal-library-tabs" role="tablist" aria-label="Comparison manuscript">
            <button type="button" role="tab" aria-selected={active === "a"} onClick={() => setActive("a")}>First record</button>
            <button type="button" role="tab" aria-selected={active === "b"} onClick={() => setActive("b")}>Second record</button>
          </div>
          <div className="signal-library-compare-grid">
            <ComparisonPane manuscriptId={a} active={active === "a"} />
            <ComparisonPane manuscriptId={b} active={active === "b"} />
          </div>
        </>
      )}
    </div>
  );
}
