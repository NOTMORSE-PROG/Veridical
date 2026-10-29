import { describe, expect, it } from "vitest";
import { workspaceStep, workspaceStepPath } from "./reviewStep";

const step = (search: string, work?: { pendingCriteria: number; openFindings: number }) =>
  workspaceStep(new URLSearchParams(search), work);

describe("manuscript review step from the address", () => {
  it("opens on the first step that still needs the instructor", () => {
    expect(step("", { pendingCriteria: 2, openFindings: 5 })).toBe("criteria");
    expect(step("", { pendingCriteria: 0, openFindings: 5 })).toBe("findings");
    expect(step("", { pendingCriteria: 0, openFindings: 0 })).toBe("decision");
  });

  it("waits for the review records before choosing a default", () => {
    expect(step("")).toBeNull();
    expect(step("view=decision")).toBeNull();
  });

  it("keeps the old decision view meaning: criteria first, otherwise the decision", () => {
    expect(step("view=decision", { pendingCriteria: 1, openFindings: 9 })).toBe("criteria");
    expect(step("view=decision", { pendingCriteria: 0, openFindings: 9 })).toBe("decision");
  });

  it("treats finding, match and explore addresses as the findings step", () => {
    for (const search of ["view=queue", "view=finding&finding=7", "view=matches", "panel=explore", "finding=7", "flag=7"]) {
      expect(step(search, { pendingCriteria: 3, openFindings: 0 })).toBe("findings");
    }
  });

  it("keeps a decision-view address on its step despite a stale finding", () => {
    expect(step("view=decision&step=grades&finding=7", { pendingCriteria: 0, openFindings: 0 })).toBe("grades");
  });

  it("lets a saved-record or criterion request win over the step the instructor was on", () => {
    expect(step("view=decision&step=criteria&criterion_record=all")).toBe("grades");
    expect(step("view=decision&step=decision&criterion=4")).toBe("criteria");
  });

  it("honours a named step and ignores an unknown one", () => {
    expect(step("view=decision&step=grades")).toBe("grades");
    expect(step("view=decision&step=nonsense", { pendingCriteria: 0, openFindings: 0 })).toBe("decision");
  });

  it("builds step addresses the workspace reads back", () => {
    const search = (path: string) => new URLSearchParams(path.slice(path.indexOf("?") + 1));
    expect(workspaceStepPath(5, "findings")).toBe("/report/5/document?view=queue&pane=review");
    expect(step(search(workspaceStepPath(5, "findings")).toString())).toBe("findings");
    for (const target of ["criteria", "grades", "decision"] as const) {
      expect(step(search(workspaceStepPath(5, target)).toString())).toBe(target);
    }
  });
});
