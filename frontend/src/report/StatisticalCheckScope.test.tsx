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

describe("separate saved reconstruction scope", () => {
  it("keeps a compatible reconstruction distinct from verified original data and GRIM coverage", () => {
    render(<StatisticalCheckScope scope={{ state: "completed", unassessed_descriptive_rows: 2,
      reconstruction: { state: "candidate_found", considered_rows: 3, attempted_rows: 3, compatible_rows: 3, unassessed_rows: 0 } }} />);
    expect(screen.getByText("Statistical checks: Not fully assessed.")).toBeVisible();
    expect(screen.getByText(/2 descriptive rows were not assessed by GRIM/)).toBeVisible();
    expect(screen.getByText(/SPRITE found compatible candidate distributions for 3 declared rows/)).toHaveTextContent("not recovered or verified original data");
    expect(screen.getByText(/Rows considered: 3; search attempted: 3; compatible candidates: 3; not assessed: 0/)).toBeVisible();
  });
  it("does not turn a bounded search failure into inconsistency or completion", () => {
    render(<StatisticalCheckScope scope={{ state: "completed", reconstruction: {
      state: "incomplete", considered_rows: 4, attempted_rows: 2, compatible_rows: 1, unassessed_rows: 3,
    } }} />);
    expect(screen.getByText("Statistical checks: Not fully assessed.")).toBeVisible();
    expect(screen.getByText(/SPRITE reconstruction remains unassessed for 3 rows/)).toHaveTextContent("Search failure is not proof of inconsistency.");
    expect(screen.queryByText(/GRIM\/GRIMMER/)).not.toBeInTheDocument();
  });
  it("retains applicability limits for zero supported reconstruction rows", () => {
    render(<StatisticalCheckScope scope={{ state: "not_applicable", reconstruction: {
      state: "not_applicable", considered_rows: 0, attempted_rows: 0, compatible_rows: 0, unassessed_rows: 0,
    } }} />);
    expect(screen.getByText(/No supported candidate rows were recorded for SPRITE reconstruction/)).toHaveTextContent("does not establish that the manuscript contains no statistics");
  });
  it.each([
    { state: "candidate_found", considered_rows: 2, attempted_rows: 1, compatible_rows: 2, unassessed_rows: 0 },
    { state: "candidate_found", considered_rows: 2, attempted_rows: 2, compatible_rows: 2, unassessed_rows: 1 },
    { state: "candidate_found", considered_rows: -1, attempted_rows: 0, compatible_rows: 0, unassessed_rows: 0 },
    { state: "candidate_found", considered_rows: null, attempted_rows: null, compatible_rows: null, unassessed_rows: null },
    { state: "candidate_found", considered_rows: "2", attempted_rows: 2, compatible_rows: 2, unassessed_rows: 0 },
    { state: "not_recorded", considered_rows: null, attempted_rows: null, compatible_rows: null, unassessed_rows: null },
  ])("does not display success from an unusable receipt (%j)", reconstruction => {
    render(<StatisticalCheckScope scope={{ state: "completed", reconstruction: reconstruction as unknown as NonNullable<StatisticalCheckScopeOut["reconstruction"]> }} />);
    expect(screen.getByText("Statistical checks: Not fully assessed.")).toBeVisible();
    expect(screen.getByText(/No SPRITE reconstruction status was recorded/)).toBeVisible();
    expect(screen.queryByText(/SPRITE found compatible/)).not.toBeInTheDocument();
  });
});
