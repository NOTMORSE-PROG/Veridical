import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Instructor, QuotaStatus, SettingsOut } from "../api/types";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SettingsPage } from "./Settings";
import { ME_QUERY_KEY } from "../auth/useAuth";

const ME: Instructor = {
  id: 1,
  email: "prof@tip.edu.ph",
  display_name: "Prof Cruz",
  onboarding_dismissed_at: null,
};

const QUOTA: QuotaStatus = {
  key_state: "configured",
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

  it.each(["save", "remove"].flatMap(operation =>
    ["other account", "same account new login"].flatMap(lifetime =>
      ["success", "failure"].map(outcome => ({ operation, lifetime, outcome }))),
  ))("ignores late key $operation $outcome after $lifetime", async ({ operation, lifetime, outcome }) => {
    let client!: QueryClient;
    function CaptureClient() { client = useQueryClient(); return <SettingsPage />; }
    let quotaReads = 0;
    let release!: (response: Response) => void;
    const held = new Promise<Response>(resolve => { release = resolve; });
    const initialSettings = { ...SETTINGS, api_key: { byok_available: true, has_own_api_key: operation === "remove" } };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(input.toString(), "http://localhost").pathname;
      if (path === "/settings/api-key") return held;
      if (path === "/quota") { quotaReads += 1; return new Response(JSON.stringify(QUOTA)); }
      return new Response(JSON.stringify(baseHandlers({ "/settings": initialSettings })[path as "/settings"]));
    }));
    renderWithProviders(<CaptureClient />, { authInstructorId: ME.id });
    await screen.findByText("Quota record: shared key");
    if (operation === "save") fireEvent.change(await screen.findByLabelText("Gemini API key"), { target: { value: "previous-owner-private-draft" } });
    fireEvent.click(await screen.findByRole("button", { name: operation === "save" ? "Save key" : "Remove key" }));
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(1));
    const pending = client.getMutationCache().getAll()[0];
    await waitFor(() => expect(pending.state.status).toBe("pending"));
    const currentSettings = { ...SETTINGS, thresholds: { ...SETTINGS.thresholds, ready_min_score: 92 } };
    const currentQuota = { ...QUOTA, calls_remaining: 173 };
    act(() => {
      if (lifetime === "same account new login") client.removeQueries({ queryKey: ME_QUERY_KEY, exact: true });
      client.setQueryData(ME_QUERY_KEY, { ...ME, id: lifetime === "other account" ? 2 : ME.id });
      client.setQueryData(["settings"], currentSettings);
      client.setQueryData(["quota"], currentQuota);
    });
    const input = await screen.findByLabelText("Gemini API key");
    // The prior account's private draft and pending action must disappear.
    const draftAfterSwitch = (input as HTMLInputElement).value;
    fireEvent.change(input, { target: { value: "current-owner-unsaved-draft" } });
    const readsBeforeCompletion = quotaReads;
    await act(async () => {
      release(outcome === "success" ? new Response(JSON.stringify({ ...initialSettings,
        api_key: { byok_available: true, has_own_api_key: operation === "save" } }))
        : new Response(JSON.stringify({ error: { code: "invalid_api_key", message: "Previous owner request failed." } }), { status: 422 }));
    });
    await waitFor(() => expect(pending.state.status).not.toBe("pending"));
    expect(client.getQueryData(["settings"])).toEqual(currentSettings);
    expect(client.getQueryData(["quota"])).toEqual(currentQuota);
    expect(quotaReads).toBe(readsBeforeCompletion);
    expect(screen.getByLabelText("Gemini API key")).toHaveValue("current-owner-unsaved-draft");
    expect(screen.getByRole("button", { name: "Save key" })).toBeEnabled();
    expect(screen.queryByText(/Your personal key was (saved|removed)/)).not.toBeInTheDocument();
    expect(screen.queryByText("Previous owner request failed.")).not.toBeInTheDocument();
    expect(draftAfterSwitch).toBe("");
  });

  it.each(["save", "remove"])("clears stale setup state after key %s and waits for the fresh ledger", async operation => {
    let quotaReads = 0;
    let release!: (response: Response) => void;
    const held = new Promise<Response>(resolve => { release = resolve; });
    const initial = { ...QUOTA, key_state: operation === "save" ? "unavailable" : "configured", key_source: operation === "save" ? "shared" : "own" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(input.toString(), "http://localhost").pathname;
      if (path === "/quota") {
        quotaReads += 1;
        return quotaReads === 1 ? new Response(JSON.stringify(initial)) : held;
      }
      if (path === "/settings/api-key") {
        expect(init?.method).toBe(operation === "save" ? "POST" : "DELETE");
        return new Response(JSON.stringify({ ...SETTINGS,
          api_key: { byok_available: true, has_own_api_key: operation === "save" } }));
      }
      const body = baseHandlers({ "/settings": { ...SETTINGS,
        api_key: { byok_available: true, has_own_api_key: operation === "remove" } } })[path as "/settings"];
      return new Response(JSON.stringify(body));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<SettingsPage />);
    await screen.findByText(operation === "save" ? /No usable AI configuration/ : "AI configuration found. Provider access has not been verified.");
    if (operation === "save") fireEvent.change(await screen.findByLabelText("Gemini API key"), { target: { value: "controlled-not-sent-to-provider" } });
    fireEvent.click(await screen.findByRole("button", { name: operation === "save" ? "Save key" : "Remove key" }));
    try {
      expect(await screen.findByText("Loading local AI budget...")).toBeVisible();
      expect(screen.queryByText("Quota record: personal key")).not.toBeInTheDocument();
      expect(screen.queryByText("Quota record: shared key")).not.toBeInTheDocument();
      release(new Response(JSON.stringify({ ...QUOTA, key_state: operation === "save" ? "configured" : "unavailable",
        key_source: operation === "save" ? "own" : "shared", calls_remaining: 17 })));
      expect(await screen.findByText(operation === "save" ? "Remaining local request budget: 17" : "Remaining budget in this local record: 17")).toBeVisible();
      expect(screen.getByText(operation === "save" ? "Quota record: personal key" : "Quota record: shared key")).toBeVisible();
      expect(quotaReads).toBe(2);
      expect(fetchMock.mock.calls.filter(([input]) => input.toString().endsWith("/settings/api-key"))).toHaveLength(1);
    } finally { release(new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled read failure" } }), { status: 503 })); }
  });

  it("keeps a successful key save separate from a failed ledger refresh", async () => {
    let reads = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(input.toString(), "http://localhost").pathname;
      if (path === "/quota") return ++reads === 1 ? new Response(JSON.stringify(QUOTA))
        : new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled read failure" } }), { status: 503 });
      if (path === "/settings/api-key") return new Response(JSON.stringify({ ...SETTINGS, api_key: { byok_available: true, has_own_api_key: true } }));
      return new Response(JSON.stringify(baseHandlers()[path as "/settings"]));
    }));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Remaining local request budget: 260");
    fireEvent.change(await screen.findByLabelText("Gemini API key"), { target: { value: "controlled-not-sent-to-provider" } });
    fireEvent.click(screen.getByRole("button", { name: "Save key" }));
    expect(await screen.findByText(/Could not load the local AI budget right now/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove key" })).toBeVisible();
    expect(screen.queryByText("AI configuration found. Provider access has not been verified.")).not.toBeInTheDocument();
    expect(screen.queryByText("Remaining local request budget: 260")).not.toBeInTheDocument();
  });

  it.each(["unavailable", "not_checked", undefined, "future"])("keeps saved budget records visible for setup state %s", async key_state => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers({ "/quota": {
      ...QUOTA, key_state, calls_remaining: 0, recorded_budget_debits: 33,
      legacy_models: [{ model: "earlier-request", recorded_budget_debits: 20, cache_hits_today: 3, budget_model: null }],
    } })));
    renderWithProviders(<SettingsPage />);
    const ledger = await screen.findByRole("region", { name: "Earlier model records for this budget day" });
    expect(within(ledger).getByRole("heading", { name: "earlier-request" })).toBeVisible();
    expect(screen.getByText("Remaining budget in this local record: 0")).toBeVisible();
    expect(screen.getByText("Recorded local budget: 33")).toBeVisible();
    const notice = key_state === "unavailable" ? "No usable AI configuration was available for this status check. Saved shared-key budget records are shown below."
      : "AI setup status is unknown. These local budget records do not establish whether AI requests can run.";
    expect(screen.getByText(notice)).toBeVisible();
    const section = screen.getByRole("region", { name: "Local AI budget" });
    expect(section.querySelector('div[aria-hidden="true"]')).toBeNull();
    expect(within(section).getAllByText(key_state === "unavailable" ? "AI setup unavailable" : "AI setup status unknown").length).toBe(2);
    expect(within(section).queryByText("Configured")).not.toBeInTheDocument();
    expect(within(section).queryByText("Unavailable for routing today")).not.toBeInTheDocument();
  });

  it("keeps earlier budget records visible and qualifies the raw counter without a cache percentage", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers({ "/quota": {
      ...QUOTA, calls_used: 20, calls_remaining: 0, recorded_budget_debits: 31,
      cache_hit_rate: null, cache_hits_today: 8,
      models: [{ ...QUOTA.models![0], model: "current-request", calls_used: 20,
        daily_limit: 20, calls_remaining: 0, recorded_budget_debits: 23,
        historical_budget_debits: 20, budget_aliases: ["earlier-request"], exhausted: true }],
      legacy_models: [{ model: "earlier-request", recorded_budget_debits: 20,
        cache_hits_today: 3, budget_model: "current-request" },
        { model: "retired-request", recorded_budget_debits: 8, cache_hits_today: 1, budget_model: null }],
    } })));
    renderWithProviders(<SettingsPage />);
    const ledger = await screen.findByRole("region", { name: "Earlier model records for this budget day" });
    expect(within(ledger).getByRole("heading", { name: "earlier-request" })).toBeVisible();
    expect(within(ledger).getByRole("heading", { name: "retired-request" })).toBeVisible();
    expect(screen.getByText("Remaining local request budget: 0")).toBeVisible();
    expect(screen.getByText("Recorded local budget: 31")).toBeVisible();
    expect(screen.getByText("Cached responses reused today: 8")).toBeVisible();
    expect(screen.getByText(/Provider allowance and current availability are not verified here/)).toBeVisible();
    expect(screen.queryByText(/%.*served from cache/)).not.toBeInTheDocument();
  });

  it.each(["cache", "generation", "test", "unknown"] as const)("labels %s model provenance separately from the requested identity", async source => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers({ "/settings": { ...SETTINGS,
      prompt_versions: [{ ...SETTINGS.prompt_versions[0], requested_model: "stored-requested-id",
        model_version: source === "unknown" ? null : "stored-provider-version", response_source: source }],
    } })));
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText(/Requested model: stored-requested-id/)).toBeVisible();
    const expected = source === "test" ? "Not applicable: test-mode response" : source === "unknown" ? "Not recorded" : "stored-provider-version";
    expect(screen.getByText(new RegExp(`Provider-reported model version.*: ${expected}`))).toBeVisible();
    if (source === "cache") expect(screen.getByText("This entry reused a stored response. No new generation is recorded by this event.")).toBeVisible();
  });

  it("shows the profile as read-only with an explicit non-editable disclosure", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    expect(await screen.findByText("Prof Cruz")).toBeInTheDocument();
    expect(screen.getByText("prof@tip.edu.ph")).toBeInTheDocument();
    expect(
      screen.getByText("Neither is editable yet. Contact your administrator if either needs to change."),
    ).toBeInTheDocument();
  });

  it("moves focus to the visible heading for every in-page section link", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    for (const [linkName, headingName] of [
      ["Account", "Profile"],
      ["Personal key", "Personal Gemini key"],
      ["Local AI budget", "Local AI budget"],
      ["Review rules", "Review rules and technical record"],
    ]) {
      const heading = screen.getByRole("heading", { name: headingName });
      expect(heading).toHaveAttribute("tabindex", "-1");
      fireEvent.click(screen.getByRole("link", { name: linkName }));
      expect(document.activeElement).toBe(heading);
    }
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
    // UX review finding (P1): this branch used to leave focus on the
    // Submit button, so a screen reader never announced the failure at
    // all (aria-describedby is only read once focus lands on the field
    // it describes). Focus must move to the invalid field itself.
    expect(document.activeElement).toBe(screen.getByLabelText("New password"));
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/auth/password"),
      expect.anything(),
    );
  });

  it("rejects a mismatched confirmation and moves focus to it (UX review finding, P1)", async () => {
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

    expect(await screen.findByText("Remaining local request budget: 260")).toBeInTheDocument();
    expect(
      screen.getByText("Test mode is active. These budget figures do not represent live provider usage."),
    ).toBeInTheDocument();
    expect(screen.getAllByText("gemini-3.5-flash").length).toBe(2); // mobile card + desktop grid
  });

  it("BUG-084: the desktop quota grid's ARIA rows have a real table ancestor, and every row has one cell per column header", async () => {
    // Per ARIA 1.2, `role="row"`/`role="columnheader"` require a
    // `table`/`grid`/`treegrid` ancestor or they're orphaned -- invisible
    // to a screen reader as a table at all. Two models here (one
    // exhausted, one not) proves the Status cell is always present, never
    // conditionally omitted -- a row with fewer cells than column headers
    // is exactly the "cell count vs header count" class BUG-055 already
    // named for this ticket's own sibling file.
    const baseModel = QUOTA.models![0];
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(
        baseHandlers({
          "/quota": {
            ...QUOTA,
            models: [baseModel, { ...baseModel, model: "gemini-3.5-pro", exhausted: true }],
          },
        }),
      ),
    );
    const { container } = renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");
    await screen.findAllByText("gemini-3.5-pro");

    const table = container.querySelector("table");
    expect(table).toBeInTheDocument();
    expect(table!.querySelector("caption")).toHaveTextContent("Configured models and local budget");

    const columnHeaderCount = table!.querySelectorAll('th[scope="col"]').length;
    expect(columnHeaderCount).toBe(6);

    const rows = table!.querySelectorAll("tr");
    expect(rows.length).toBeGreaterThan(0);
    for (const row of Array.from(rows)) {
      // Every role="row" resolves to the SAME table via .closest -- none
      // orphaned outside it.
      expect(row.closest("table")).toBe(table);
    }
    // The header row's own cells are columnheaders, not plain cells; every
    // DATA row (the rest) carries exactly one role="cell" per column.
    const dataRows = Array.from(rows).slice(1);
    expect(dataRows.length).toBe(2); // one per model
    for (const row of dataRows) {
      expect(row.querySelectorAll('td, th[scope="row"]').length).toBe(columnHeaderCount);
      expect(row.querySelector('th[scope="row"]')).toBeInTheDocument();
    }
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
    expect(screen.getByRole("heading", { name: "Models and prompts recorded for your manuscripts" })).toBeInTheDocument();
    expect(screen.getByText(/Activity without a manuscript ownership link/)).toBeInTheDocument();
  });

  it("discloses missing attributable activity without claiming no calls occurred", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(baseHandlers({ "/settings": { ...SETTINGS, prompt_versions: [] } })),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(
      await screen.findByText("No attributable model activity is available for your manuscripts yet. This does not mean no AI calls have occurred."),
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
      await screen.findByText(/Personal API keys are not available on this deployment/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Gemini API key")).not.toBeInTheDocument();
  });

  it("shows the create form when BYOK is available and no key is set", async () => {
    vi.stubGlobal("fetch", stubFetchByPath(baseHandlers()));
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");

    expect(await screen.findByLabelText("Gemini API key")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save key" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Google AI Studio/ })).toHaveAttribute(
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
      "A personal Gemini key is saved for this account. Future AI requests can use it when it can be read and is accepted by the service. Available capacity depends on the provider's project limits.",
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
    expect(screen.getByLabelText("Gemini API key")).toHaveValue("bad-key");
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
    expect(await screen.findByText("Quota record: personal key")).toBeInTheDocument();
  });

  it("suppresses the key-source pill in fake mode (numbers are simulated either way)", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath(baseHandlers({ "/quota": { ...QUOTA, mode: "fake", key_source: "shared" } })),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByText("Prof Cruz");
    await screen.findByText("Test mode is active. These budget figures do not represent live provider usage.");
    expect(screen.queryByText("Quota record: shared key")).not.toBeInTheDocument();
    expect(screen.queryByText("Quota record: personal key")).not.toBeInTheDocument();
  });
});
