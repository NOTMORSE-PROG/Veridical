// Same look as the comparison-lead pager, shared by every long list that is
// split into pages instead of one long scroll.
export function Pagination({ label, page, pageSize, total, onPage }: { label: string; page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return <div className="signal-pagination">
    <p className="signal-field-hint" aria-live="polite">Showing {first} to {last} of {total}</p>
    <nav className="signal-comparison-pagination" aria-label={label}>
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      {Array.from({ length: pages }, (_, index) => index + 1).map(number => <button type="button" key={number} aria-label={`Page ${number}`}
        aria-current={page === number ? "page" : undefined} onClick={() => onPage(number)}>{number}</button>)}
      <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </nav>
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
