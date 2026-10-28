import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { RecordedEvidencePage } from "./RecordedEvidencePage";
import { SharedEvidencePacket } from "./SharedEvidencePacket";
import type { EvidenceManifest, EvidenceReference } from "./evidenceTypes";

const reference: EvidenceReference = { reference_id: "a".repeat(64), criterion_id: 4, flag_id: null,
  role: "criterion_evidence", index: 0, quote: "", anchor: null,
  source_scope: "own_manuscript", source_state: "not_checked" };
const manifest: EvidenceManifest = { snapshot_id: "b".repeat(64), assessment_revision: 0, llm_mode: "real", references: [reference] };
afterEach(() => vi.unstubAllGlobals());

describe("source availability actions", () => {
  it("does not offer or fetch an original page when neither a passage nor location was saved", () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    renderWithProviders(<SharedEvidencePacket token="test-link" manifest={manifest} references={[reference]} />);
    expect(screen.getByText("No source location was saved for this item.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Open original|Open saved source|original-page preview/ })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("applies the same unavailable-location rule to the instructor source control", () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    renderWithProviders(<RecordedEvidencePage checkRunId={5} snapshot={manifest.snapshot_id} reference={reference} />, { authInstructorId: 1 });
    expect(screen.getByText("No source location was saved for this item.")).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("retains a full quote without inventing a page and keeps its public request credential-free", async () => {
    const quoted = { ...reference, quote: "The complete recorded quotation remains readable without a saved anchor." };
    const fetch = stubFetchByPath({ [`/shared/test-link/evidence/${reference.reference_id}/page`]: {
      snapshot_id: manifest.snapshot_id, placement: { reference_id: reference.reference_id, state: "unavailable", page: null, paragraph: null, boxes: [], reason: "No source position was verified." },
      image_data_url: null, caption: "No source position was verified.",
    } });
    vi.stubGlobal("fetch", fetch);
    renderWithProviders(<SharedEvidencePacket token="test-link" manifest={manifest} references={[quoted]} />);
    expect(screen.getByText(quoted.quote)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Open saved source" }));
    expect(await screen.findByRole("dialog", { name: "Original source: Saved source" })).toBeVisible();
    expect(await screen.findByText("No original-page image is available for this evidence.")).toBeVisible();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: "omit" });
    expect(screen.queryByText(/page 1\b/i)).not.toBeInTheDocument();
  });
});
