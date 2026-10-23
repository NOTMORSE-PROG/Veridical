// Existing upload component controls count upload POSTs. Session GETs now
// have their own explicit, authenticated fixture; they never enter that mock.
import { waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";
import { renderWithProviders } from "./renderWithProviders";

export async function renderWithUploadProviders(ui: ReactElement, options: Parameters<typeof renderWithProviders>[1] = {}) {
  const actorId = options.authInstructorId ?? options.uploadInstructorId ?? 1;
  const uploadFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    if (new URL(String(input), "http://localhost").pathname.endsWith("/auth/session")) {
      return new Response(JSON.stringify({ instructor: {
        id: actorId, email: "upload@example.test", display_name: "Upload control", onboarding_dismissed_at: null,
      } }));
    }
    return uploadFetch(input, init);
  });
  const out = renderWithProviders(ui, { ...options, uploadInstructorId: actorId });
  await waitFor(() => {
    if (!document.querySelector('input[type="file"]:not(:disabled)')) throw new Error("Upload scope not ready");
  });
  return out;
}
