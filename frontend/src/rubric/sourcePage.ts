import { createContext, useContext } from "react";

// What "Show page" asks the instructor to find on that page. Levels carry a
// table position but no page coordinates, so the original cannot be marked
// at the exact spot; the source pane shows this beside the page instead.
export interface SourceTarget {
  // "Page 1 · row "Purpose" · column "PROFICIENT 3"".
  location: string;
  // The cell's own text where the level was found, or the wording to compare.
  text: string | null;
  // "format": the page a whole-format warning asks to compare the criteria with.
  kind: "cell" | "compare" | "row" | "format";
}

// Lets a criterion's "Show page" button open that page of the original.
// Null while the original is known to have no pages to show (a DOCX format,
// or no stored original).
export type ShowSourcePage = (page: number, returnTo: HTMLElement | null, target?: SourceTarget) => void;
export const ShowSourcePageContext = createContext<ShowSourcePage | null>(null);
export function useShowSourcePage(): ShowSourcePage | null {
  return useContext(ShowSourcePageContext);
}
