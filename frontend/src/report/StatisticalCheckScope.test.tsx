import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StatisticalCheckScopeOut } from "../api/types";
import { StatisticalCheckScope } from "./StatisticalCheckScope";

describe("saved statistical processing scope", () => {
  it("qualifies saved processing when descriptive assumptions remain unassessed", () => {
    render(<StatisticalCheckScope scope={{ state: "completed", unassessed_descriptive_rows: 2 }} />);
    expect(screen.getByRole("region", { name: "What the statistical check covered" })).toHaveTextContent("Not fully run");
    expect(screen.getByText(/2 descriptive rows were not assessed by GRIM\/GRIMMER/)).toBeVisible();
    expect(screen.queryByText("Completed")).not.toBeInTheDocument();
  });
  it.each([
    ["completed", "Completed", /does not mean every reported value is correct/],
    ["not_applicable", "Not applicable", /does not mean the paper has no statistics/],
    ["incomplete", "Not fully run", /Anything not checked is not treated as correct/],
    ["not_recorded", "Not recorded", /do not assume it ran/],
  ] as const)("shows %s without making a correctness judgment", (state, label, detail) => {
    render(<StatisticalCheckScope scope={{ state }} />);
    expect(screen.getByRole("region", { name: "What the statistical check covered" })).toHaveTextContent(label);
    expect(screen.getByText(detail)).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([undefined, null, {}, { state: "all_clear" }, { state: "__proto__" }])(
    "does not infer a result from missing or unsupported responses (%j)",
    (scope) => {
      render(<StatisticalCheckScope scope={scope as StatisticalCheckScopeOut} />);
      expect(screen.getByRole("region", { name: "What the statistical check covered" })).toHaveTextContent("Not recorded");
      expect(screen.queryByText("Completed")).not.toBeInTheDocument();
    },
  );
});

describe("separate saved reconstruction scope", () => {
  it("keeps a compatible reconstruction distinct from verified original data and GRIM coverage", () => {
    render(<StatisticalCheckScope scope={{ state: "completed", unassessed_descriptive_rows: 2,
      reconstruction: { state: "candidate_found", considered_rows: 3, attempted_rows: 3, compatible_rows: 3, unassessed_rows: 0 } }} />);
    expect(screen.getByRole("region", { name: "What the statistical check covered" })).toHaveTextContent("Not fully run");
    expect(screen.getByText(/2 descriptive rows were not assessed by GRIM/)).toBeVisible();
    // A possible set of answers is never presented as the real data.
    expect(screen.getByText(/For 3 table rows, it found at least one set of whole-number answers/)).toHaveTextContent("not that they are the real data");
    expect(screen.queryByText(/Rows considered/)).not.toBeInTheDocument();
  });
  it("does not turn a bounded search failure into inconsistency or completion", () => {
    render(<StatisticalCheckScope scope={{ state: "completed", reconstruction: {
      state: "incomplete", considered_rows: 4, attempted_rows: 2, compatible_rows: 1, unassessed_rows: 3,
    } }} />);
    expect(screen.getByRole("region", { name: "What the statistical check covered" })).toHaveTextContent("Not fully run");
    expect(screen.getByText(/It could not check 3 table rows/)).toHaveTextContent("Not finding a match is not proof of an error.");
    expect(screen.queryByText(/GRIM\/GRIMMER/)).not.toBeInTheDocument();
  });
  it("retains applicability limits for zero supported reconstruction rows", () => {
    render(<StatisticalCheckScope scope={{ state: "not_applicable", reconstruction: {
      state: "not_applicable", considered_rows: 0, attempted_rows: 0, compatible_rows: 0, unassessed_rows: 0,
    } }} />);
    expect(screen.getByText(/It did not run, because no table gave what it needs/)).toHaveTextContent("This does not mean the paper has no statistics.");
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
    expect(screen.getByRole("region", { name: "What the statistical check covered" })).toHaveTextContent("Not fully run");
    expect(screen.getByText(/No record of whether this step ran was saved/)).toBeVisible();
    expect(screen.queryByText(/found at least one set of whole-number answers/)).not.toBeInTheDocument();
  });
});
