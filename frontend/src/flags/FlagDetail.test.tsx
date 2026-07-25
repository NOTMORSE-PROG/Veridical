import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { FlagDetailPage } from "./FlagDetail";

const FLAG = {
  id: 5,
  check_result_id: 9,
  check_kind: "citation_integrity",
  criterion_text: null,
  severity: "high",
  confidence: 1.0,
  evidence_excerpt: "Wang, S. (2019). A study of things.",
  page_anchor: "page 34",
  annotation: null,
  overridden: false,
  override_reason: null,
  ai_verdict_summary: "not_supported",
  ai_reasoning: "This source appears in the Retraction Watch database.",
};

describe("FlagDetailPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the evidence excerpt, anchor, and AI verdict", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByText(/Wang, S\. \(2019\)/)).toBeInTheDocument();
    expect(screen.getByText("page 34")).toBeInTheDocument();
    expect(screen.getByText(/AI verdict: not_supported/)).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
  });

  it("requires a reason before the override Confirm button enables", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/flags/5": FLAG }));
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    const confirm = await screen.findByRole("button", { name: "Confirm override" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("Why are you overriding this finding?"), {
      target: { value: "Checked myself." },
    });
    expect(confirm).toBeEnabled();
  });

  it("submits the override with the reason and shows the trail afterward", async () => {
    const overriddenFlag = {
      ...FLAG,
      overridden: true,
      override_reason: "Checked myself — not actually retracted.",
      report: { check_run_id: 1, results: [], status: "ready" },
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/flags/5": FLAG, "/flags/5/override": overriddenFlag }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    fireEvent.click(screen.getByRole("button", { name: "Override" }));
    fireEvent.change(await screen.findByPlaceholderText("Why are you overriding this finding?"), {
      target: { value: "Checked myself — not actually retracted." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm override" }));

    expect(
      await screen.findByText(/Instructor overrode this finding/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Checked myself — not actually retracted\./)).toBeInTheDocument();
    // Original AI finding still shown, never destroyed:
    expect(screen.getAllByText(/not_supported/).length).toBeGreaterThan(0);
  });

  it("saves an annotation", async () => {
    const annotated = { ...FLAG, annotation: "Confirmed with the adviser." };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/flags/5": FLAG, "/flags/5/annotate": annotated }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    await screen.findByText(/Wang, S\./);
    const box = screen.getByRole("textbox", { name: /Annotation/i });
    fireEvent.change(box, { target: { value: "Confirmed with the adviser." } });
    fireEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("shows an error banner when the flag fails to load", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/flags/5": new Response(
          JSON.stringify({ error: { code: "not_found", message: "No flag 5." } }),
          { status: 404 },
        ),
      }),
    );
    renderWithProviders(<FlagDetailPage />, { route: "/flags/5", path: "/flags/:flagId" });
    expect(await screen.findByRole("alert")).toHaveTextContent("No flag 5.");
  });
});
