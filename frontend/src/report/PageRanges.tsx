import { Fragment } from "react";
import { pageRanges } from "../domain/criterionSourceCoverage";

// "45-47, 53", with each range kept on one line so "179-186" never wraps at
// its hyphen. The hyphen stays a plain one so find-in-page and copy work.
export function PageRanges({ pages }: { pages: number[] }) {
  return <>{pageRanges(pages).split(", ").map((range, index) => <Fragment key={range}>
    {index > 0 && ", "}<span className="signal-page-range">{range}</span>
  </Fragment>)}</>;
}
