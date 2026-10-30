import { createContext, useContext } from "react";

// Lets a criterion's "Show page" button open that page of the original.
// Null while the original is known to have no pages to show (a DOCX format,
// or no stored original).
export type ShowSourcePage = (page: number, returnTo: HTMLElement | null) => void;
export const ShowSourcePageContext = createContext<ShowSourcePage | null>(null);
export function useShowSourcePage(): ShowSourcePage | null {
  return useContext(ShowSourcePageContext);
}
