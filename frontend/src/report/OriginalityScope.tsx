// V-077 (D-028): what the originality check compared against, stated next
// to the integrity signals so an empty result over an empty Library can
// never read as a clean bill of health (charter judgment 9: N/A is not
// "passed"). A count, not a judgment.
import type { OriginalityScope as Scope } from "../api/types";
import { ActionLink } from "../ui/ActionLink";
import { Alert } from "../ui/Alert";
import { LibraryPassageScope } from "./LibraryPassageScope";
import { ORIGINALITY_NOT_COMPARED } from "./originalityCoverage";

export function OriginalityScope({
  scope,
  showAddAction = false,
}: {
  scope: Scope | null | undefined;
  showAddAction?: boolean;
}) {
  if (!scope) return null;
  if (scope.not_compared_reason === "source_unresolved") {
    return <p className="signal-originality-scope">Originality and reuse: not assessed because the manuscript could not be read reliably. The original paper is still available.</p>;
  }
  if (scope.not_compared_reason === "reference_boundary_unclear") {
    return <p className="signal-originality-scope">{ORIGINALITY_NOT_COMPARED.boundaryScope}</p>;
  }
  if (scope.not_compared_reason === "reference_list_unconfirmed") {
    return <p className="signal-originality-scope">{ORIGINALITY_NOT_COMPARED.unconfirmedScope}</p>;
  }
  if (scope.not_compared_reason === "no_text") {
    return (
      <p className="signal-originality-scope">
        Originality and reuse: no eligible text was available for this comparison, so the Library comparison did not run. This is not evidence of originality.
      </p>
    );
  }
  const count = scope.compared_count;
  if (count === null) {
    return (
      <p className="signal-originality-scope">
        Originality and reuse: the number of manuscripts this check compared against was not recorded.
      </p>
    );
  }
  if (count === 0) {
    return (
      <Alert tone="info" title="Originality had nothing to compare against">
        <p>
          {scope.corpus === "library"
            ? "No Library records were available to compare against when this check ran, so no reuse could be found. This is not evidence of originality. Add finished manuscripts to the Library so future checks have something to compare against."
            : "No stored manuscripts were available to compare against when this check ran, so no reuse could be found. This is not evidence of originality."}
        </p>
        {showAddAction && scope.corpus === "library" && (
          <ActionLink to="/library/add" variant="secondary">Add to Library</ActionLink>
        )}
      </Alert>
    );
  }
  const noun = count === 1 ? "record" : "records";
  return (
    <>
    <p className="signal-originality-scope">
      {scope.corpus === "library"
        ? `Originality and reuse: compared against ${count} Library ${noun} when this check ran. Manuscripts that are not in the Library were not compared.`
        : `Originality and reuse: compared against ${count} stored ${noun} when this check ran, before the Library became a separate collection.`}
    </p>
    <LibraryPassageScope summary={scope.library_passage_indexing} />
    </>
  );
}
