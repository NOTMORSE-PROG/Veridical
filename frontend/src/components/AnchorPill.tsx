// Small "where to look" locator chip (page/section reference) — shared
// by screens 4h and 4i, both of which show evidence tied to a spot in
// the manuscript.
export function AnchorPill({ anchor }: { anchor: string }) {
  return (
    <span className="w-fit max-w-full rounded-full bg-status-neutral-bg px-2 py-0.5 text-xs break-words text-ink-tertiary">
      {anchor}
    </span>
  );
}
