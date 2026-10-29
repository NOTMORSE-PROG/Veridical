import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

// The bar above the manuscript and review panes holds "back" and position
// controls, so they sit in the same top-left place for every detail view
// instead of floating inside the review pane.
export const WorkspaceBarContext = createContext<HTMLElement | null>(null);

export function InWorkspaceBar({ children }: { children: ReactNode }) {
  const slot = useContext(WorkspaceBarContext);
  return slot ? createPortal(children, slot) : <>{children}</>;
}

export { LeadingChevronIcon } from "../ui/ActionLink";
