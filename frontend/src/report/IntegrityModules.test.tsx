import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { IntegrityModuleNavigation } from "./IntegrityModules";

it("says one saved record in the singular", () => {
  render(<IntegrityModuleNavigation selected="internal_agreement" onSelect={() => {}} controls="panel"
    counts={new Map([["internal_agreement", 1], ["citation_integrity", 2]])} />);
  expect(screen.getByRole("button", { name: /^Internal agreement/ })).toHaveTextContent("1 saved record");
  expect(screen.getByRole("button", { name: /^Internal agreement/ })).not.toHaveTextContent("1 saved records");
  expect(screen.getByRole("button", { name: /^Citation integrity/ })).toHaveTextContent("2 saved records");
});
