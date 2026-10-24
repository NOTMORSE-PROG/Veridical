// F7, screen 4x: factual saved preparation scope, including historical unknowns.
import type { PassageIndexing } from "../api/types";
import { passageIndexing, passageIndexingText } from "./passageIndexing";

export function PassageIndexingDetails({ value }: { value: PassageIndexing | null | undefined }) {
  const scope = passageIndexing(value);
  return <section className="signal-library-passage-indexing">
    <h2>Automatic passage matching</h2>
    <p>{passageIndexingText(scope)}</p>
    {scope.state !== "complete" && <p>Full-document and section comparisons have separate evidence.</p>}
  </section>;
}
