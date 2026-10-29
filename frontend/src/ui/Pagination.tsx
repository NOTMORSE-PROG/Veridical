// Same look as the comparison-lead pager, shared by every long list that is
// split into pages instead of one long scroll. Only a window of page numbers
// is drawn, so a list with thousands of pages stays one short row.
const PAGE_WINDOW = 2;

export function pageWindow(page: number, pages: number): (number | "gap")[] {
  const shown = new Set<number>([1, pages]);
  for (let number = page - PAGE_WINDOW; number <= page + PAGE_WINDOW; number++) {
    if (number >= 1 && number <= pages) shown.add(number);
  }
  const sorted = [...shown].sort((left, right) => left - right);
  const items: (number | "gap")[] = [];
  sorted.forEach((number, index) => {
    if (index > 0 && number - sorted[index - 1] > 1) items.push("gap");
    items.push(number);
  });
  return items;
}

export function PageNumbers({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  return <>
    <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
    {pageWindow(page, pages).map((item, index) => item === "gap"
      ? <span key={`gap-${index}`} className="signal-pagination__gap" aria-hidden="true">…</span>
      : <button type="button" key={item} aria-label={`Page ${item}`} aria-current={page === item ? "page" : undefined} onClick={() => onPage(item)}>{item}</button>)}
    <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
  </>;
}

export function Pagination({ label, page, pageSize, total, onPage }: { label: string; page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return <div className="signal-pagination">
    <p className="signal-field-hint" aria-live="polite">Showing {first} to {last} of {total.toLocaleString()}</p>
    <nav className="signal-comparison-pagination" aria-label={label}><PageNumbers page={page} pages={pages} onPage={onPage} /></nav>
  </div>;
}

export function pageOf<T>(items: T[], page: number, pageSize: number): T[] {
  return items.slice((page - 1) * pageSize, page * pageSize);
}

export function clampPage(raw: string | null, total: number, pageSize: number): number {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const value = Number(raw);
  return Number.isInteger(value) && value >= 1 ? Math.min(value, pages) : 1;
}
