import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ShareLinkOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { ShareModal } from "./ShareModal";

const ACTIVE_LINK: ShareLinkOut = {
  token: "abc123token",
  check_run_id: 5,
  created_at: "2026-08-15T10:00:00Z",
  expires_at: null,
};

describe("ShareModal", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows a loading state, then the create-link form when no link is active", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/share": null }));
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    expect(screen.getByText("Checking for an existing link.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Create link" })).toBeInTheDocument();
    expect(screen.getByText(/Treat this link as semi-confidential/)).toBeInTheDocument();
    expect(screen.getByText(/G1's status, score, flags,\s*evidence, and any decision note/)).toBeInTheDocument();
  });

  it("creating a link shows the URL, copy button, and metadata", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/share" && init?.method === "POST") {
        return new Response(JSON.stringify(ACTIVE_LINK), { status: 200 });
      }
      if (path === "/check-runs/5/share") return new Response(JSON.stringify(null), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Create link" }));

    const input = await screen.findByDisplayValue(`${window.location.origin}/shared/abc123token`);
    expect(input).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument();
    expect(screen.getByText(/No expiry set\./)).toBeInTheDocument();
    // ux-critic findings (P1): the URL field must have a real accessible
    // name (a screen reader previously announced only "edit text, read
    // only"), and focus must move onto it once it appears (the form it
    // replaces disappears from the DOM with nothing else claiming focus).
    expect(input).toHaveAccessibleName("Report share link");
    await waitFor(() => expect(document.activeElement).toBe(input));
  });

  it("copy button writes to the clipboard and announces success", async () => {
    const writeText = vi.fn(async () => {});
    Object.assign(navigator, { clipboard: { writeText } });
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/share": ACTIVE_LINK }));
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Copy link" }));

    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/shared/abc123token`);
    await screen.findByRole("button", { name: "Copied!" });
    expect(screen.getByText("Link copied to clipboard.")).toBeInTheDocument();
  });

  it("shows a manual-copy fallback when the clipboard API fails", async () => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(async () => {
          throw new Error("denied");
        }),
      },
    });
    vi.stubGlobal("fetch", stubFetchByPath({ "/check-runs/5/share": ACTIVE_LINK }));
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Copy link" }));

    expect(
      await screen.findByText("Couldn't copy automatically. Select the link text above and copy it manually."),
    ).toBeInTheDocument();
  });

  it("revoke requires confirmation before the endpoint is called", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/share" && init?.method === "DELETE") {
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      if (path === "/check-runs/5/share") return new Response(JSON.stringify(ACTIVE_LINK), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Revoke link" }));
    const confirmText = await screen.findByText(/Revoke this link\?/);
    expect(confirmText).toBeInTheDocument();
    // ux-critic finding (P1, live-reproduced): opening this sub-panel
    // used to leave focus wherever it was (or drop it to <body>), the
    // same "Modal that wasn't actually one" bug class this app already
    // fixed once elsewhere (BUG-020) -- a screen-reader user got no
    // signal a destructive confirmation had appeared.
    expect(document.activeElement).toBe(confirmText);
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/check-runs/5/share"),
      expect.objectContaining({ method: "DELETE" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Revoke link" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/check-runs/5/share"),
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("revoking flips the modal back to the create-link state", async () => {
    let revoked = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/share" && init?.method === "DELETE") {
        revoked = true;
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      if (path === "/check-runs/5/share") {
        return new Response(JSON.stringify(revoked ? null : ACTIVE_LINK), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Revoke link" }));
    fireEvent.click(await screen.findByRole("button", { name: "Revoke link" }));

    expect(await screen.findByRole("button", { name: "Create link" })).toBeInTheDocument();
  });

  it("regenerate requires confirmation and issues a new token", async () => {
    const NEW_LINK: ShareLinkOut = { ...ACTIVE_LINK, token: "brand-new-token" };
    let calls = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/share" && init?.method === "POST") {
        calls += 1;
        return new Response(JSON.stringify(NEW_LINK), { status: 200 });
      }
      if (path === "/check-runs/5/share") return new Response(JSON.stringify(ACTIVE_LINK), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Regenerate link" }));
    const confirmText = await screen.findByText(/Replace this link with a new one\?/);
    expect(confirmText).toBeInTheDocument();
    expect(document.activeElement).toBe(confirmText);
    expect(calls).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "Create new link" }));
    await screen.findByDisplayValue(`${window.location.origin}/shared/brand-new-token`);
    expect(calls).toBe(1);
  });

  it("shows the server's error message on a failed create", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/check-runs/5/share" && init?.method === "POST") {
        return new Response(
          JSON.stringify({ error: { code: "not_found", message: "No check run with id 5." } }),
          { status: 404 },
        );
      }
      if (path === "/check-runs/5/share") return new Response(JSON.stringify(null), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ShareModal checkRunId={5} manuscriptLabel="G1" onClose={() => {}} />);

    fireEvent.click(await screen.findByRole("button", { name: "Create link" }));

    const error = await screen.findByText("No check run with id 5.");
    expect(error.closest('[role="alert"]')).toBeInTheDocument();
    expect(document.activeElement).toBe(error.closest('[role="alert"]'));
  });
});
