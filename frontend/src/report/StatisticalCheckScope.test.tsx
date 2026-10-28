import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StatisticalCheckScopeOut } from "../api/types";
import { StatisticalCheckScope } from "./StatisticalCheckScope";

describe("saved statistical processing scope", () => {
  it("qualifies saved processing when descriptive assumptions remain unassessed", () => {
    render(<StatisticalCheckScope scope={{ state: "completed", unassessed_descriptive_rows: 2 }} />);
    expect(screen.getByText("Statistical checks: Not fully assessed.")).toBeVisible();
    expect(screen.getByText(/2 descriptive rows were not assessed by GRIM\/GRIMMER/)).toBeVisible();
    expect(screen.queryByText(/Completion recorded/)).not.toBeInTheDocument();
  });
  it.each([
    ["completed", "Completion recorded.", /does not establish that every reported value is correct/],
    ["not_applicable", "Not applicable.", /does not establish that the manuscript contains no statistics/],
    ["incomplete", "Not fully assessed.", /Unassessed content is not treated as passed/],
    ["not_recorded", "Not recorded.", /Do not infer that the check ran/],
  ] as const)("shows %s without making a correctness judgment", (state, label, detail) => {
    render(<StatisticalCheckScope scope={{ state }} />);
    expect(screen.getByText(`Statistical checks: ${label}`)).toBeVisible();
    expect(screen.getByText(detail)).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([undefined, null, {}, { state: "all_clear" }, { state: "__proto__" }])(
    "does not infer a result from missing or unsupported responses (%j)",
    (scope) => {
      render(<StatisticalCheckScope scope={scope as StatisticalCheckScopeOut} />);
      expect(screen.getByText("Statistical checks: Not recorded.")).toBeVisible();
      expect(screen.queryByText(/Completion recorded/)).not.toBeInTheDocument();
    },
  );
});
