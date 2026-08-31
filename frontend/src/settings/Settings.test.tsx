import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Instructor, QuotaStatus, SettingsOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SettingsPage } from "./Settings";

const ME: Instructor = {
  id: 1,
  email: "prof@tip.edu.ph",
  display_name: "Prof Cruz",
  onboarding_dismissed_at: null,
};

const QUOTA: QuotaStatus = {
  mode: "live",
  key_source: "shared",
  quota_day: "2026-08-14",
  calls_used: 40,
  daily_limit: 300,
  calls_remaining: 260,
  cache_hits_today: 10,
  cache_hit_rate: 0.2,
  reset_at: "2026-08-15T00:00:00Z",
  rpm_limit: 15,
  models: [
    {
      model: "gemini-3.5-flash",
      calls_used: 40,
      daily_limit: 300,
      calls_remaining: 260,
      cache_hits_today: 10,
      rpm_limit: 15,
      vision: true,
      exhausted: false,
    },
  ],
};

const SETTINGS: SettingsOut = {
  thresholds: {
    ready_min_score: 85,
    not_ready_max_score: 60,
    escalation_agreement_threshold: 1.0,
    editable: false,
  },
  prompt_versions: [
    {
      prompt_type: "semantic_grading",
      prompt_version: "v2",
      model: "gemini-3.5-flash",
      observed_at: "2026-08-14T00:00:00Z",
    },
  ],
  api_key: { byok_available: true, has_own_api_key: false },
};

function baseHandlers(overrides: Record<string, unknown> = {}) {
  return {
    "/auth/session": { instructor: ME },
    "/quota": QUOTA,
    "/settings": SETTINGS,
    ...overrides,
  };
}

describe("SettingsPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the profile as read-only with an explicit non-editable disclosure", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText("Prof Cruz")).toBeInTheDocument();
    expect(screen.getByText("prof@tip.edu.ph")).toBeInTheDocument();
    expect(
      screen.getByText("Neither is editable yet. Contact your administrator if either needs to change."),
    ).toBeInTheDocument();
  });

  it("blocks submit and shows an inline error when the current password is missing", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Enter your current password.")).toBeInTheDocument();
  });

  it("rejects a new password under 8 characters client-side, without a round trip", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/auth/password") throw new Error("must not be called");
      const handlers = baseHandlers();
      const body = handlers[path as keyof typeof handlers];
      return new Response(JSON.stringify(body), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "s3cret!" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "short" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    const error = await screen.findByText("Use at least 8 characters.");
    expect(error).toBeInTheDocument();
    // ux-critic finding (P1): this branch used to leave focus on the
    // Submit button, so a screen reader never announced the failure at
    // all (aria-describedby is only read once focus lands on the field
    // it describes). Focus must move to the invalid field itself.
    expect(document.activeElement).toBe(screen.getByLabelText("New password"));
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/auth/password"),
      expect.anything(),
    );
  });

  it("rejects a mismatched confirmation and moves focus to it (ux-critic finding, P1)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "s3cret!" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-pw" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "different" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText("This doesn't match the new password above.")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByLabelText("Confirm new password"));
  });

  it("submits, clears the form, and announces success", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/auth/password" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({ current_password: "s3cret!", new_password: "brand-new-pw" });
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      const handlers = baseHandlers();
      return new Response(JSON.stringify(handlers[path as keyof typeof handlers]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    const currentInput = screen.getByLabelText("Current password") as HTMLInputElement;
    const nextInput = screen.getByLabelText("New password") as HTMLInputElement;
    const confirmInput = screen.getByLabelText("Confirm new password") as HTMLInputElement;
    fireEvent.change(currentInput, { target: { value: "s3cret!" } });
    fireEvent.change(nextInput, { target: { value: "brand-new-pw" } });
    fireEvent.change(confirmInput, { target: { value: "brand-new-pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    // One sr-only live-region announcement + one visible confirmation line
    // -- both real, both intentional (spec: screen-reader users get the
    // live-region announcement, sighted users get the inline text).
    await waitFor(() => expect(screen.getAllByText("Password changed.").length).toBe(2));
    await waitFor(() => expect(currentInput.value).toBe(""));
    expect(nextInput.value).toBe("");
    expect(confirmInput.value).toBe("");
  });

  it("shows the server's honest error on a wrong current password", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/auth/password" && init?.method === "POST") {
        return new Response(
          JSON.stringify({ error: { code: "unauthenticated", message: "Current password is incorrect." } }),
          { status: 401 },
        );
      }
      const handlers = baseHandlers();
      return new Response(JSON.stringify(handlers[path as keyof typeof handlers]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: "wrong" } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "brand-new-pw" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "brand-new-pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    const error = await screen.findByText("Current password is incorrect.");
    expect(error.closest('[role="alert"]')).toBeInTheDocument();
  });

  it("renders the quota meter and per-model detail, and flags fake mode honestly", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(baseHandlers({ "/quota": { ...QUOTA, mode: "fake" } })),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(await screen.findByText("40 of 300 AI calls used today (87% left)")).toBeInTheDocument();
    expect(
      screen.getByText("Running in test mode. These numbers are simulated, not real API usage."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("gemini-3.5-flash").length).toBe(2); // mobile card + desktop grid
  });

  it("renders named readiness bands, keeps raw cutoffs in the technical record, and shows real prompt/model versions", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(await screen.findByText(/Ready: the recorded evidence supports proceeding/)).toBeInTheDocument();
    expect(screen.getByText(/Not Ready: the recorded criteria show substantial unresolved gaps/)).toBeInTheDocument();
    expect(screen.getByText(/These thresholds are fixed for every instructor right now/)).toBeInTheDocument();
    expect(screen.getByText("Ready composite cutoff").closest("div")).toHaveTextContent("85");
    expect(screen.getByText("Not Ready composite cutoff").closest("div")).toHaveTextContent("60");
    expect(screen.getByText("Escalation agreement cutoff").closest("div")).toHaveTextContent("1.00");
    expect(screen.getByText(/semantic_grading/)).toBeInTheDocument();
  });

  it("discloses an honest empty state when no AI calls have been recorded yet", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(baseHandlers({ "/settings": { ...SETTINGS, prompt_versions: [] } })),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(
      await screen.findByText("No AI grading calls have been recorded yet. This fills in automatically once VERIDICAL grades a manuscript."),
    ).toBeInTheDocument();
  });

  it("shows a disabled explanation, never a form, when BYOK isn't available on this deployment", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(
        baseHandlers({
          "/settings": { ...SETTINGS, api_key: { byok_available: false, has_own_api_key: false } },
        }),
      ),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(
      await screen.findByText(/Bringing your own API key isn't available on this deployment yet/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Gemini API key")).not.toBeInTheDocument();
  });

  it("shows the create form when BYOK is available and no key is set", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(await screen.findByLabelText("Gemini API key")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save key" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Get a free key from Google AI Studio/ })).toHaveAttribute(
      "href",
      "https://aistudio.google.com/app/apikey",
    );
  });

  it("blocks submit and focuses the field when the key is empty", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.click(await screen.findByRole("button", { name: "Save key" }));
    expect(await screen.findByText("Paste your Gemini API key.")).toBeInTheDocument();
    expect(document.activeElement).toBe(screen.getByLabelText("Gemini API key"));
  });

  it("saves a valid key, moves focus to the configured state, and never echoes the key", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/settings/api-key" && init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        expect(body).toEqual({ api_key: "AIzaSy-real-looking-key" });
        return new Response(
          JSON.stringify({ ...SETTINGS, api_key: { byok_available: true, has_own_api_key: true } }),
          { status: 200 },
        );
      }
      const handlers = baseHandlers();
      return new Response(JSON.stringify(handlers[path as keyof typeof handlers]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.change(await screen.findByLabelText("Gemini API key"), {
      target: { value: "AIzaSy-real-looking-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    const configured = await screen.findByText(
      "Your own Gemini API key is active. AI grading now uses your quota, not the shared pool.",
    );
    expect(configured).toBeInTheDocument();
    expect(document.activeElement).toBe(configured);
    expect(screen.getByRole("button", { name: "Remove key" })).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("AIzaSy-real-looking-key");
  });

  it("shows the server's error on an invalid key and does not flip to configured", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/settings/api-key" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            error: {
              code: "invalid_api_key",
              message: "Gemini rejected this key. Check that it's correct and still active, then try again.",
            },
          }),
          { status: 422 },
        );
      }
      const handlers = baseHandlers();
      return new Response(JSON.stringify(handlers[path as keyof typeof handlers]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.change(await screen.findByLabelText("Gemini API key"), {
      target: { value: "bad-key" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));

    const error = await screen.findByText(
      "Gemini rejected this key. Check that it's correct and still active, then try again.",
    );
    expect(error.closest('[role="alert"]')).toBeInTheDocument();
    expect(document.activeElement).toBe(error.closest('[role="alert"]'));
    expect(screen.queryByRole("button", { name: "Remove key" })).not.toBeInTheDocument();
  });

  it("removing a key reverts to the create form and moves focus back to the input", async () => {
    let removed = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/settings/api-key" && init?.method === "DELETE") {
        removed = true;
        return new Response(
          JSON.stringify({ ...SETTINGS, api_key: { byok_available: true, has_own_api_key: false } }),
          { status: 200 },
        );
      }
      if (path === "/settings") {
        return new Response(
          JSON.stringify({
            ...SETTINGS,
            api_key: { byok_available: true, has_own_api_key: !removed },
          }),
          { status: 200 },
        );
      }
      const handlers = baseHandlers();
      return new Response(JSON.stringify(handlers[path as keyof typeof handlers]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    fireEvent.click(await screen.findByRole("button", { name: "Remove key" }));

    const input = await screen.findByLabelText("Gemini API key");
    expect(document.activeElement).toBe(input);
    expect(screen.getByRole("button", { name: "Save key" })).toBeInTheDocument();
  });

  it("quota meter shows which key is in use", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(baseHandlers({ "/quota": { ...QUOTA, key_source: "own" } })),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");
    expect(await screen.findByText("Using your own key")).toBeInTheDocument();
  });

  it("suppresses the key-source pill in fake mode (numbers are simulated either way)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(baseHandlers({ "/quota": { ...QUOTA, mode: "fake", key_source: "shared" } })),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");
    await screen.findByText("Running in test mode. These numbers are simulated, not real API usage.");
    expect(screen.queryByText("Using the shared key")).not.toBeInTheDocument();
    expect(screen.queryByText("Using your own key")).not.toBeInTheDocument();
  });
});
