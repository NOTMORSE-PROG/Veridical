import { fireEvent, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import { pendingRouteReturnElement } from "../routing/useRouteFocus";
import { renderWithProviders } from "../test/renderWithProviders";
import { ActionLink } from "../ui/ActionLink";
import { focusReportSection } from "./reportSectionNavigation";

function SectionControl() {
  const location = useLocation();
  return <><ActionLink to="/report/5?queue=needs_review#coverage-statement-heading" onClick={event => focusReportSection(event, location, "coverage-statement-heading")}>Review assessment limits</ActionLink>
    <h2 id="coverage-statement-heading" tabIndex={-1}>Assessment limits</h2></>;
}

it("scrolls and focuses an attached same-report target after the router jump", async () => {
  const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
  renderWithProviders(<SectionControl />, { route: "/report/5?queue=needs_review" });
  fireEvent.click(screen.getByRole("link", { name: "Review assessment limits" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "Assessment limits" })).toHaveFocus());
  expect(scroll).toHaveBeenCalledWith({ block: "start" });
  scroll.mockRestore();
});

it("registers the full destination for a workspace transition instead of focusing the outgoing DOM", () => {
  renderWithProviders(<SectionControl />, { route: "/report/5/document?queue=needs_review" });
  fireEvent.click(screen.getByRole("link", { name: "Review assessment limits" }));
  expect(pendingRouteReturnElement("/report/5?queue=needs_review")).toBe("coverage-statement-heading");
  expect(screen.getByRole("heading", { name: "Assessment limits" })).not.toHaveFocus();
});

it("leaves modified link clicks to the browser without claiming focus", () => {
  renderWithProviders(<SectionControl />, { route: "/report/5" });
  fireEvent.click(screen.getByRole("link", { name: "Review assessment limits" }), { ctrlKey: true });
  expect(screen.getByRole("heading", { name: "Assessment limits" })).not.toHaveFocus();
});
