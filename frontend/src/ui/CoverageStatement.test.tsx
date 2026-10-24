import { fireEvent, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { renderWithProviders } from "../test/renderWithProviders";
import { CoverageStatement, type CoverageStatementItem } from "./CoverageStatement";

it("keeps distinct causes visible while retaining repeated source records in named details", () => {
  const items: CoverageStatementItem[] = [
    { key: "citations", label: "Citations", detail: "Reference claims were not assessed.",
      subIssues: ["Two references lack metadata."], sources: [
        { label: "Recorded equation at paragraph 8", to: "/report/5/document?anchor=paragraph%208" },
        { label: "Recorded equation at paragraph 8", to: "/report/5/document?anchor=paragraph%208" },
      ] },
    { key: "agreement", label: "Agreement", detail: "Study roles could not be linked." },
  ];
  renderWithProviders(<CoverageStatement state="partial" headingId="limits" heading="Assessment limits" intro="Unchecked content is not passed." items={items} />);
  expect(screen.getByText("Reference claims were not assessed.")).toBeVisible();
  expect(screen.getByText("Study roles could not be linked.")).toBeVisible();
  expect(screen.getByText("Two references lack metadata.")).not.toBeVisible();
  const disclosure = screen.getByRole("button", { name: "Show Citations assessment details" });
  const panel = document.getElementById(disclosure.getAttribute("aria-controls")!)!;
  expect(disclosure).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(disclosure);
  expect(disclosure).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("Two references lack metadata.")).toBeVisible();
  expect(within(panel).getAllByRole("link", { name: "Recorded equation at paragraph 8" })).toHaveLength(2);
  fireEvent.click(disclosure);
  expect(panel).not.toBeVisible();
});

it("shares only identical operations and destinations, retaining distinct recovery and source links", () => {
  const common = { to: "/report/5/document?view=decision", label: "Open the paper" };
  const items: CoverageStatementItem[] = [
    { key: "one", label: "One", detail: "First cause", action: common },
    { key: "two", label: "Two", detail: "Second cause", action: { ...common } },
    { key: "native", label: "Native equations", detail: "Native content was not assessed", action: { to: "/report/5/document?coverage=native_math", label: "Inspect native equation coverage" } },
    { key: "recheck", label: "Retry", detail: "Check again", action: { to: common.to, label: "Start another review" } },
  ];
  renderWithProviders(<CoverageStatement state="partial" headingId="limits" heading="Assessment limits" intro="Unchecked content is not passed." items={items} />);
  expect(screen.getAllByRole("link")).toHaveLength(3);
  expect(screen.getByRole("link", { name: "Open manuscript for manual review" })).toHaveAttribute("href", common.to);
  expect(screen.getByRole("link", { name: "Inspect native equation coverage" })).toHaveAttribute("href", "/report/5/document?coverage=native_math");
  expect(screen.getByRole("link", { name: "Start another review" })).toHaveAttribute("href", common.to);
});
