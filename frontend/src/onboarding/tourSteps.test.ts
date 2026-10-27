import { afterEach, describe, expect, it } from "vitest";
import { findTourAnchor, TOUR_STEPS } from "./tourSteps";

function mountAnchor(dataTour: string) {
  const el = document.createElement("button");
  el.setAttribute("data-tour", dataTour);
  // jsdom doesn't lay out real geometry -- getBoundingClientRect
  // returns all-zero by default, which findTourAnchor's own
  // "width > 0 && height > 0" check would (correctly, for a real
  // browser) treat as "not actually rendered." Stub a real-looking
  // rect so these tests exercise the DOM-presence branch, not the
  // rendered-in-a-real-layout-engine branch (jsdom can't give us that).
  el.getBoundingClientRect = () =>
    ({ top: 0, left: 0, width: 100, height: 40, bottom: 40, right: 100 }) as DOMRect;
  document.body.appendChild(el);
  return el;
}

function stepById(id: string) {
  const step = TOUR_STEPS.find((candidate) => candidate.id === id);
  if (!step) throw new Error(`missing tour step ${id}`);
  return step;
}

describe("findTourAnchor", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("finds a step's control when it is rendered on the step's own screen", () => {
    const el = mountAnchor("upload-format-cta");
    expect(findTourAnchor(stepById("upload-format"), "/dashboard")).toBe(el);
  });

  it("returns null on a screen the step does not belong to, even if a matching control exists", () => {
    mountAnchor("upload-format-cta");
    expect(findTourAnchor(stepById("upload-format"), "/rubric")).toBeNull();
  });

  it("returns null when the step's control has not mounted (AC5: never a pointer aimed at nothing)", () => {
    expect(findTourAnchor(stepById("new-check"), "/dashboard")).toBeNull();
  });

  it("falls back to the visible desktop Account control when the replay action is closed", () => {
    mountAnchor("replay-tour-account");
    expect(findTourAnchor(stepById("replay"), "/dashboard")?.getAttribute("data-tour")).toBe("replay-tour-account");
  });

  it("ignores a matching selector with a zero-size rect (present in DOM but not actually rendered)", () => {
    const el = mountAnchor("confirm-rubric-cta");
    el.getBoundingClientRect = () => ({ top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }) as DOMRect;
    expect(findTourAnchor(stepById("confirm-rubric"), "/rubric/3/review")).toBeNull();
  });
});

describe("TOUR_STEPS", () => {
  it("BUG-091: every step says where its control lives, so an unanchored step is still useful", () => {
    for (const step of TOUR_STEPS) expect(step.whereHint.trim().length).toBeGreaterThan(0);
  });
});
