import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CitationRecencySettingsOut } from "../api/types";
import { renderWithProviders } from "../test/renderWithProviders";
import { CitationRecencySetting } from "./CitationRecencySetting";

const FAMILY = "22222222-2222-2222-2222-222222222222";
const DEFAULTS: CitationRecencySettingsOut = {
  enabled: false, window_years: 5, reference_year_basis: "manuscript_year", counts_toward_readiness: false,
  setting_sources: { enabled: "default", window_years: "default", reference_year_basis: "default", counts_toward_readiness: "default" },
  saved_setting_invalid: false,
};

function serve(settings: CitationRecencySettingsOut, put: (body: unknown) => Response) {
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/rubrics/10") return new Response(JSON.stringify({ id: 10, rubric_family_id: FAMILY, citation_recency: settings }), { status: 200 });
    if (path === `/rubric-families/${FAMILY}/citation-recency` && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      bodies.push(body);
      return put(body);
    }
    return new Response("not found", { status: 404 });
  }));
  return bodies;
}

describe("the format's citation recency setting", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("turns the note on with a window and says where each value comes from", async () => {
    const bodies = serve(DEFAULTS, (body) => new Response(JSON.stringify({ ...DEFAULTS, ...(body as object),
      setting_sources: { enabled: "format", window_years: "format", reference_year_basis: "format", counts_toward_readiness: "format" } }), { status: 200 }));
    renderWithProviders(<CitationRecencySetting familyId={FAMILY} rubricId={10} />);
    const toggle = await screen.findByRole("checkbox", { name: /Note sources older than the window/ });
    expect(toggle).toHaveAccessibleName("Note sources older than the window (default)");
    fireEvent.click(toggle);
    fireEvent.change(screen.getByLabelText(/Window \(years\)/), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Save recency setting" }));
    expect(await screen.findByText(/New checks use this setting; existing reports keep the values they were checked with/)).toBeInTheDocument();
    // Only what changed is sent; the rest keeps following the defaults.
    expect(bodies[0]).toEqual({ enabled: true, window_years: 7 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Use the defaults" })).toBeInTheDocument());
  });

  it("refuses a window outside 1 to 100 before sending, and shows a server refusal at the window", async () => {
    const bodies = serve({ ...DEFAULTS, enabled: true, setting_sources: { ...DEFAULTS.setting_sources, enabled: "format" } }, () =>
      new Response(JSON.stringify({ detail: [{ type: "value_error", loc: ["body", "window_years"], msg: "Value error, Use a window of 1 to 100 years." }] }), { status: 422 }));
    renderWithProviders(<CitationRecencySetting familyId={FAMILY} rubricId={10} />);
    const window = await screen.findByLabelText(/Window \(years\)/);
    fireEvent.change(window, { target: { value: "0" } });
    expect(window).toHaveAccessibleDescription("Enter a whole number of years from 1 to 100.");
    expect(screen.getByRole("button", { name: "Save recency setting" })).toBeDisabled();
    fireEvent.change(window, { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save recency setting" }));
    expect(await screen.findByText("Use a window of 1 to 100 years. Nothing was saved.")).toBeInTheDocument();
    expect(window).toHaveAttribute("aria-invalid", "true");
    await waitFor(() => expect(window).toHaveFocus());
    // Nothing scheduled after the refusal may take focus back to Save.
    await act(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(window).toHaveFocus();
    expect(bodies).toEqual([{ window_years: 12 }]);
  });

  it("clears the format's own values with Use the defaults, and warns about a saved setting that no longer validates", async () => {
    const bodies = serve({ ...DEFAULTS, enabled: true, saved_setting_invalid: true, setting_sources: { ...DEFAULTS.setting_sources, enabled: "format" } },
      () => new Response(JSON.stringify(DEFAULTS), { status: 200 }));
    renderWithProviders(<CitationRecencySetting familyId={FAMILY} rubricId={10} />);
    expect(await screen.findByText(/saved recency setting is no longer valid/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use the defaults" }));
    await waitFor(() => expect(bodies).toEqual([{}]));
  });
});
