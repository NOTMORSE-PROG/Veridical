// Screen 4w-Compare (V-066) — two library records side by side. Each
// column reuses LibraryContentPane exactly (§8 of the ui-designer spec):
// no second rendering path, no computed similarity score between the two
// (that's what a real F7 flag + PassagePairPanel already do; this screen
// is "read them side by side yourself," matching the owner's own
// iThenticate framing).
import { useRef } from "react";
import { Link, useSearchParams } from "react-router";
import { manuscriptIdentity } from "../domain/manuscriptLabel";
import { useRouteFocus } from "../routing/useRouteFocus";
import { LibraryContentPane } from "./LibraryContentPane";
import { useLibraryItem } from "./useLibrary";

function bannerText(aOwn: boolean, bOwn: boolean): string {
  if (aOwn && bOwn) return "Comparing two of your own manuscripts. Both are fully visible.";
  if (!aOwn && !bOwn) {
    return "Comparing two manuscripts from VERIDICAL's shared library. Both are shown as bounded excerpts, to protect the instructors' manuscripts.";
  }
  return "Comparing your manuscript with another instructor's. Yours is fully visible; theirs is a bounded excerpt, to protect that instructor's manuscript.";
}

function CompareColumn({ manuscriptId }: { manuscriptId: number }) {
  const { data: item, isPending, isError, refetch } = useLibraryItem(manuscriptId);

  if (isPending) {
    return (
      <div role="status" aria-live="polite" aria-busy="true" className="flex-1 p-4 text-sm text-ink-secondary">
        Loading manuscript.
      </div>
    );
  }
  if (isError || !item) {
    return (
      <div role="alert" className="flex-1 p-4 text-sm text-status-attention-text">
        This manuscript couldn't be loaded.{" "}
        <button type="button" onClick={() => refetch()} className="font-medium underline">
          Try again
        </button>
        .
      </div>
    );
  }

  const identity = item.title
    ? { primary: item.title, secondary: item.group_label }
    : manuscriptIdentity(item.group_label, item.original_filename);

  return (
    // newcomer finding (V-066 review, 2026-08-23): reproduced live at
    // 390px and 1280px -- without an explicit `overflow-hidden` here, this
    // column's own height was governed entirely by its grid row's "auto"
    // sizing (no bound from the parent grid either, see the fix in
    // LibraryComparePage below), so the two stacked columns' content
    // overlapped instead of stacking. `overflow-hidden` here + a real
    // bounded grid row on the parent together give this column an actual
    // height for its own children to scroll WITHIN, rather than growing
    // past it.
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden border border-border">
      <div className="flex items-center gap-2 border-b border-border bg-status-neutral-bg px-3 py-2">
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${item.is_own ? "bg-status-info-bg text-status-info-text" : "bg-status-neutral-bg text-status-neutral-text"}`}
        >
          {item.is_own ? "Yours" : "Another instructor's"}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink" title={identity.primary}>
          {identity.primary}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden">
        <LibraryContentPane manuscriptId={manuscriptId} isOwn={item.is_own} />
      </div>
    </div>
  );
}

export function LibraryComparePage() {
  const [searchParams] = useSearchParams();
  const a = Number(searchParams.get("a"));
  const b = Number(searchParams.get("b"));
  const headingRef = useRef<HTMLHeadingElement>(null);
  useRouteFocus("Compare - Library - VERIDICAL", headingRef);

  const itemA = useLibraryItem(Number.isFinite(a) ? a : undefined);
  const itemB = useLibraryItem(Number.isFinite(b) ? b : undefined);

  if (!Number.isFinite(a) || !Number.isFinite(b)) {
    return (
      <div className="p-6 text-sm text-ink-secondary">
        Choose two manuscripts from the{" "}
        <Link to="/library" className="font-medium text-link underline hover:text-link-hover">
          library
        </Link>{" "}
        to compare.
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col">
      <header className="flex flex-col gap-1 border-b border-border px-4 py-2.5 sm:px-6">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-ink-tertiary">
          <Link to="/library" className="text-link underline hover:text-link-hover">
            Library
          </Link>
          <span aria-hidden="true">/</span>
          <span>Compare</span>
        </nav>
        <h1 ref={headingRef} tabIndex={-1} className="text-lg font-bold text-ink">
          Compare manuscripts
        </h1>
        {itemA.data && itemB.data && (
          <p className="text-sm text-ink-secondary">{bannerText(itemA.data.is_own, itemB.data.is_own)}</p>
        )}
      </header>
      {/* newcomer finding (V-066 review, 2026-08-23): `overflow-y-auto` on
          this grid combined with un-bounded ("auto") row tracks let each
          stacked column grow to its full content height instead of being
          clipped to a real row -- the second column's header rendered on
          top of the first column's content. `grid-rows-[minmax(0,1fr)_minmax(0,1fr)]`
          gives the mobile-stacked layout two REAL, equal, bounded rows
          (each `CompareColumn` then scrolls its own content within its
          own row, via the `overflow-hidden` fix on that component) --
          `overflow-hidden` here instead of `overflow-y-auto`, since
          nothing should scroll at this level once rows are bounded. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)_minmax(0,1fr)] gap-0.5 overflow-hidden bg-border lg:grid-cols-2 lg:grid-rows-1">
        <CompareColumn manuscriptId={a} />
        <CompareColumn manuscriptId={b} />
      </div>
    </div>
  );
}
