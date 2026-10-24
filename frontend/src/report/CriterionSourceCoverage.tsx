import type { ReactNode } from "react";
import type { CriterionSourceCoverage as Coverage } from "../api/types";
import { criterionCoverageLocations } from "../domain/criterionSourceCoverage";
import { instructorAnchor } from "../domain/sourceAnchor";

export function CriterionSourceCoverage({ value, sourceAction, firstSlot, criterionText }: {
  value: Coverage; sourceAction: (anchor: string, slot: number) => ReactNode; firstSlot: number; criterionText: string;
}) {
  const state = value.native_math_inventory_state;
  if (state === "not_applicable") return null;
  if (state === "not_recorded" || state === "unavailable") return <p><strong>{state === "not_recorded" ? "Source coverage not recorded." : "Source coverage unavailable."}</strong>{" "}
    {state === "not_recorded" ? "Native Word equation inventory was not recorded for this assessment." : "The saved native Word equation inventory could not be verified."}{" "}
    Equation presence and count are unknown.</p>;
  const total = value.n_native_equations_unassessed!;
  if (total === 0) return <p className="signal-field-hint">The saved main-document-body inventory recorded no native Word equations.</p>;
  const unlocated = value.n_native_equations_unlocated!;
  return <div className="signal-attached-flow">
    <p><strong>Source content omitted.</strong> The saved assessment input omitted {total} native Word equation {total === 1 ? "occurrence" : "occurrences"}.
      {value.native_math_excluded_paragraphs.length > 0 && " Text and table content affected by those equations was excluded too."}</p>
    {unlocated > 0 && <p>{unlocated} equation {unlocated === 1 ? "occurrence has" : "occurrences have"} no recorded source position. The saved source record does not establish that the remaining text and tables could be used safely.</p>}
    <details><summary aria-label={`Source scope and locations: ${criterionText}`}>Source scope and locations</summary><div className="signal-attached-flow">
      <p>This inventory covers the main document body. It does not establish coverage of other document parts.</p>
      <ul>{criterionCoverageLocations(value).map((item, index) => <li key={`${item.kind}:${item.paragraph}`}>
        <p>{item.paragraph === null ? "Source position not recorded" : `${instructorAnchor(`paragraph ${item.paragraph}`)}${item.kind === "table_cell" ? ", table" : ""}`}: {item.count} equation {item.count === 1 ? "occurrence" : "occurrences"} omitted.</p>
        {item.paragraph !== null && sourceAction(`paragraph ${item.paragraph}`, firstSlot + index)}
      </li>)}</ul>
    </div></details>
  </div>;
}
