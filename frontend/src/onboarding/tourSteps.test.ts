import { afterEach, describe, expect, it } from "vitest";
import { resolveTourStep, TOUR_STEPS } from "./tourSteps";

function mountAnchor(dataTour: string) {
  const el = document.createElement("button");
  el.setAttribute("data-tour", dataTour);
  // jsdom doesn't lay out real geometry -- getBoundingClientRect
  // returns all-zero by default, which resolveTourStep's own
  // "width > 0 && height > 0" check would (correctly, for a real
  // browser) treat as "not actually rendered." Stub a real-looking
  // rect so these tests exercise the DOM-presence branch, not the
  // rendered-in-a-real-layout-engine branch (jsdom can't give us that).
  el.getBoundingClientRect = () =>
    ({ top: 0, left: 0, width: 100, height: 40, bottom: 40, right: 100 }) as DOMRect;
  document.body.appendChild(el);
  return el;
}

describe("resolveTourStep", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("resolves the first step when its anchor exists on the current page", () => {
    mountAnchor("upload-format-cta");
    const resolved = resolveTourStep(0, "/dashboard");
    expect(resolved?.step.id).toBe("upload-format");
    expect(resolved?.index).toBe(0);
  });

  it("skips a step whose route doesn't match the current page", () => {
    // Step 0 is dashboard-only; on a report page, resolution should
    // skip straight to the escalation step (index 3) if its anchor
    // exists, never getting stuck on step 0.
    mountAnchor("escalated-panel");
    const resolved = resolveTourStep(0, "/report/9");
    expect(resolved?.step.id).toBe("escalation");
  });

  it("skips a step whose anchor is not in the DOM yet (AC5: never a pointer aimed at nothing)", () => {
    // Nothing mounted for "new-check-cta" -- only the step after it.
    mountAnchor("replay-tour-desktop");
    const resolved = resolveTourStep(2, "/dashboard");
    // Step 2 (new-check) has no anchor; step 4 (replay) matches any
    // route and its anchor exists -- resolution lands there, not on
    // a null/broken step 2.
    expect(resolved?.step.id).toBe("replay");
  });

  it("returns null when nothing in the remaining steps resolves on this page", () => {
    // Nothing mounted at all, and starting past the always-matching
    // replay step.
    const resolved = resolveTourStep(TOUR_STEPS.length, "/dashboard");
    expect(resolved).toBeNull();
  });

  it("tries anchorSelectors in order and uses the first real match (desktop/mobile dual-render)", () => {
    mountAnchor("replay-tour-mobile"); // desktop selector absent, mobile present
    const resolved = resolveTourStep(4, "/dashboard");
    expect(resolved?.step.id).toBe("replay");
    expect(resolved?.el.getAttribute("data-tour")).toBe("replay-tour-mobile");
  });

  it("ignores a matching selector with a zero-size rect (present in DOM but not actually rendered)", () => {
    const el = mountAnchor("confirm-rubric-cta");
    el.getBoundingClientRect = () => ({ top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }) as DOMRect;
    const resolved = resolveTourStep(1, "/rubric/3/review");
    expect(resolved).toBeNull();
  });
});
