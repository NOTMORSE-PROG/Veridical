import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders as renderAppWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { AuditLogPage } from "./AuditLog";

function renderWithProviders(...args: Parameters<typeof renderAppWithProviders>) {
  return renderAppWithProviders(args[0], { authInstructorId: 1, ...args[1] });
}

// Mobile card + desktop table both render in the DOM simultaneously (CSS
// `sm:hidden`/`hidden sm:block` — jsdom applies no media queries), so every
// query here expects TWO matches, not one (same convention as
// dashboard/ManuscriptsTable.test.tsx and rubric/Manage.test.tsx).

const PAGE = {
  items: [
    {
      id: 42,
      event_type: "llm_call",
      check_run_id: 7,
      manuscript_group_label: "G-11",
      prompt_type: "semantic_grading",
      prompt_version: "v1",
      agreement_score: 0.667,
      created_at: "2026-07-25T14:05:00Z",
    },
    {
      id: 41,
      event_type: "escalation_resolved",
      check_run_id: 7,
      manuscript_group_label: "G-11",
      prompt_type: null,
      prompt_version: null,
      agreement_score: 0.667,
      created_at: "2026-07-25T14:18:00Z",
    },
  ],
  total: 2,
  page: 1,
  page_size: 25,
};

const DETAIL = {
  ...PAGE.items[0],
  input_hash: "abc123",
  payload: { prompt_type: "semantic_grading", prompt: "Grade this...", response: { verdicts: [] } },
};

describe("AuditLogPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the actual historical Return and full reason without expanding Technical", async () => {
    const row = { ...PAGE.items[0], event_type: "report_decided", prompt_type: null };
    const note = "SANDBOX stored reason <img src='private'> & αβ";
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": { ...PAGE, items: [row], total: 1 }, "/audit/42": {
      ...row, payload: { instructor_id: 1, decision: "returned", note, status: "needs_review",
        pending_criteria: [{ criterion_id: 35, check_result_id: 178, outcome: "escalated" }] },
    } }));
    renderWithProviders(<AuditLogPage />, { route: "/audit" });
    fireEvent.click((await screen.findAllByRole("button", { name: "Detail" }))[0]);
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("Returned for revision")).toBeVisible();
    expect(within(dialog).getByText(note)).toBeVisible();
    expect(within(dialog).getByText("1 recorded item.")).toBeVisible();
    expect(within(dialog).getByText("You")).toBeVisible();
    expect(within(dialog).getByText("Technical record for reproducibility").parentElement).not.toHaveAttribute("open");
    expect(dialog.querySelector("img")).toBeNull();
  });

  it("keeps the historical named-level gap explicit and does not attribute another actor to the reader", async () => {
    const row = { ...PAGE.items[1], id: 328 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": { ...PAGE, items: [row], total: 1 }, "/audit/328": {
      ...row, payload: { instructor_id: 2, resolution: "mark_level", criterion_id: 35, check_result_id: 178, new_score: 50, ai_majority_verdict: "Proficient", reason: "SANDBOX actual recorded judgment reason." },
    } }));
    renderWithProviders(<AuditLogPage />, { route: "/audit" });
    fireEvent.click((await screen.findAllByRole("button", { name: "Detail" }))[0]);
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(/selected level name is unavailable/)).toBeVisible();
    expect(within(dialog).getByText("Instructor #2")).toBeVisible();
    expect(within(dialog).getByText("SANDBOX actual recorded judgment reason.")).toBeVisible();
    expect(within(dialog).queryByText("Satisfactory")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("You")).not.toBeInTheDocument();
  });

  it("opening and closing Detail preserves the selected Audit page", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": { ...PAGE, page: 2, total: 60 }, "/audit/42": DETAIL }));
    renderWithProviders(<AuditLogPage />, { route: "/audit?page=2" });
    expect(await screen.findByText("Page 2 of 3")).toBeVisible();
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    await screen.findByRole("dialog");
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.getByText("Page 2 of 3")).toBeVisible();
  });

  it.each([401, 403, 404])("hides both cached ordinary and raw private detail after terminal %s", async status => {
    let client!: QueryClient;
    let denied = false;
    function Probe() { client = useQueryClient(); return <AuditLogPage />; }
    const privateDetail = { ...DETAIL, event_type: "report_decided", payload: { decision: "returned", note: "PRIVATE-OLD-DECISION-REASON", instructor_id: 1 } };
    const fetcher = stubFetchByPath({ "/audit": PAGE, "/audit/42": privateDetail });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (denied && String(input).endsWith("/audit/42")) return new Response(JSON.stringify({ error: { code: "not_found", message: "This entry is unavailable." } }), { status });
      return fetcher(input);
    }));
    renderWithProviders(<Probe />, { route: "/audit?detail=42" });
    expect(await screen.findByText("PRIVATE-OLD-DECISION-REASON")).toBeVisible();
    denied = true;
    await client.invalidateQueries({ queryKey: ["audit-log"] });
    expect(await screen.findByText("This entry is unavailable.")).toBeVisible();
    expect(screen.queryByText("PRIVATE-OLD-DECISION-REASON")).not.toBeInTheDocument();
    expect(screen.queryByText(/"note": "PRIVATE-OLD-DECISION-REASON"/)).not.toBeInTheDocument();
  });

  it("qualifies a retained same-entry 503 snapshot instead of presenting it as a current read", async () => {
    let client!: QueryClient;
    let unavailable = false;
    function Probe() { client = useQueryClient(); return <AuditLogPage />; }
    const fetcher = stubFetchByPath({ "/audit": PAGE, "/audit/42": { ...DETAIL, event_type: "report_decided", payload: { decision: "returned", note: "Authorized last loaded reason." } } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => unavailable && String(input).endsWith("/audit/42")
      ? new Response(JSON.stringify({ error: { code: "api_down", message: "Temporarily unavailable." } }), { status: 503 }) : fetcher(input)));
    renderWithProviders(<Probe />, { route: "/audit?detail=42" });
    expect(await screen.findByText("Authorized last loaded reason.")).toBeVisible();
    unavailable = true;
    await client.invalidateQueries({ queryKey: ["audit-log"] });
    expect(await screen.findByText(/Last loaded entry/)).toBeVisible();
    expect(screen.getByText("Authorized last loaded reason.")).toBeVisible();
  });

  it.each(["heading", "newer focus", "closed"])("hands Retry focus to its persistent heading and preserves %s on late completion", async intent => {
    let release!: (response: Response) => void;
    let requested = 0;
    const fetcher = stubFetchByPath({ "/audit": PAGE });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).endsWith("/audit/42")) return fetcher(input);
      if (requested++ === 0) return new Response(JSON.stringify({ error: { code: "api_down", message: "Controlled unavailable entry." } }), { status: 503 });
      return new Promise<Response>(resolve => { release = resolve; });
    }));
    renderWithProviders(<AuditLogPage />, { route: "/audit?detail=42" });
    const retry = await screen.findByRole("button", { name: "Retry audit entry" });
    retry.focus(); fireEvent.click(retry);
    const heading = screen.getByRole("heading", { name: "Audit entry #42" });
    expect(heading).toHaveFocus();
    const close = screen.getByRole("button", { name: "Close" });
    if (intent === "newer focus") close.focus();
    else if (intent === "closed") fireEvent.click(close);
    await waitFor(() => expect(release).toBeDefined());
    release(new Response(JSON.stringify(DETAIL)));
    if (intent === "closed") {
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(screen.getByRole("heading", { name: "Audit" })).toHaveFocus();
    } else {
      await screen.findByText(/VERIDICAL completed/);
      expect(intent === "heading" ? heading : close).toHaveFocus();
    }
  });

  it.each([401, 403, 404])("hides cached private list metadata after a terminal list %s", async status => {
    let client!: QueryClient;
    let denied = false;
    function Probe() { client = useQueryClient(); return <AuditLogPage />; }
    const fetcher = stubFetchByPath({ "/audit": { ...PAGE, items: [{ ...PAGE.items[0], manuscript_group_label: "PRIVATE-OLD-GROUP" }] } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => denied && String(input).includes("/audit?")
      ? new Response(JSON.stringify({ error: { code: "not_found", message: "Private list unavailable." } }), { status }) : fetcher(input)));
    renderWithProviders(<Probe />, { route: "/audit" });
    expect((await screen.findAllByText(/PRIVATE-OLD-GROUP/)).length).toBeGreaterThan(0);
    denied = true;
    await client.invalidateQueries({ queryKey: ["audit-log"] });
    await waitFor(() => expect(screen.queryByText(/PRIVATE-OLD-GROUP/)).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Detail" })).not.toBeInTheDocument();
  });

  it("revokes old private content immediately when the authenticated reader changes", async () => {
    let client!: QueryClient;
    let secondReader = false;
    function Probe() { client = useQueryClient(); return <AuditLogPage />; }
    const fetcher = stubFetchByPath({ "/audit": PAGE, "/audit/42": { ...DETAIL, event_type: "report_decided", payload: { decision: "returned", note: "FIRST-READER-PRIVATE" } } });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => secondReader && String(input).endsWith("/audit/42")
      ? new Response(JSON.stringify({ error: { code: "not_found", message: "No owned entry." } }), { status: 404 }) : fetcher(input)));
    renderWithProviders(<Probe />, { route: "/audit?detail=42" });
    expect(await screen.findByText("FIRST-READER-PRIVATE")).toBeVisible();
    secondReader = true;
    client.setQueryData(ME_QUERY_KEY, { id: 2 });
    await waitFor(() => expect(screen.queryByText("FIRST-READER-PRIVATE")).not.toBeInTheDocument());
    expect(await screen.findByText("No owned entry.")).toBeVisible();
  });

  it("lists audit events with their labels", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("AI call")).length).toBe(2);
    expect(screen.getAllByText("Criterion judgment recorded").length).toBe(2);
  });

  it("V-078: names stored-file deletion consistently and explains retained history", async () => {
    const row = { ...PAGE.items[0], event_type: "manuscript_purged", prompt_type: null };
    vi.stubGlobal("fetch", stubFetchByPath({
      "/audit": { ...PAGE, items: [row], total: 1 },
      "/audit/42": { ...row, input_hash: null, payload: { manuscript_id: 9 } },
    }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("Stored file deleted")).length).toBe(2);
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(/An instructor deleted the uploaded file and its extracted text/))
      .toHaveTextContent("Saved reports, decisions, comparison evidence and audit history are kept.");
    expect(screen.getByText(/cannot be used as a source in future originality checks/)).toBeInTheDocument();
    expect(screen.getByText("manuscript_purged", { selector: "code" })).toBeInTheDocument();
    expect(screen.getByText(/"manuscript_id": 9/)).toBeInTheDocument();
  });

  it("V-081: explains record order without rewriting contrary historical times", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE, "/audit/42": DETAIL }));
    renderWithProviders(<AuditLogPage />, { route: "/audit" });
    expect(await screen.findByText(/Order: Highest entry number first/)).toHaveTextContent(
      "Stored times are shown unchanged and may not follow this order.",
    );
    const table = await screen.findByRole("table");
    const rows = await within(table).findAllByRole("row");
    expect(rows[1]).toHaveTextContent("Entry #42");
    expect(rows[2]).toHaveTextContent("Entry #41");
    fireEvent.click(within(rows[1]).getByRole("button", { name: "Detail" }));
    expect(await screen.findByText(DETAIL.created_at)).toBeInTheDocument();
    expect(screen.getByText("Stored time")).toBeInTheDocument();
    expect(screen.getByText(/Entry numbers identify records, not verified event times/)).toBeInTheDocument();
  });

  it.each([
    ["2026-07-25T14:05:00Z", "2026-07-25T14:18:00Z"],
    ["2026-07-25T14:18:00Z", "2026-07-25T14:05:00Z"],
    ["2026-07-25T14:05:00Z", "2026-07-25T14:05:00Z"],
  ])("V-081: grouped records retain API order without inferring a duration (%s, %s)", async (firstTime, secondTime) => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": {
      ...PAGE, items: [
        { ...PAGE.items[0], id: 42, created_at: firstTime },
        { ...PAGE.items[0], id: 38, created_at: secondTime },
      ],
    } }));
    renderWithProviders(<AuditLogPage />, { route: "/audit" });
    expect(await screen.findByText("Grouped entries")).toBeInTheDocument();
    expect(screen.queryByText(/events between/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows[2]).toHaveTextContent("Entry #42");
    expect(rows[3]).toHaveTextContent("Entry #38");
    expect(within(table).getAllByRole("button", { name: "Detail" })).toHaveLength(2);
    expect(within(table).getByRole("button", { name: "Hide" })).toHaveAttribute("aria-expanded", "true");
  });

  it("BUG (found live): the event-type key for an overridden flag never matched the real backend value, so it always showed a raw string instead of a label", async () => {
    const overridden = {
      items: [{ ...PAGE.items[0], id: 43, event_type: "flag_overridden" }],
      total: 1,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": overridden }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("Flag overridden")).length).toBe(2);
    expect(screen.queryAllByText("flag_overridden")).toHaveLength(0);
  });

  it("marks an instructor-initiated event distinctly from an automated one (icon meaning carried in text too, never color/shape alone)", async () => {
    const mixed = {
      items: [
        { ...PAGE.items[0], id: 44, event_type: "llm_call" },
        { ...PAGE.items[0], id: 45, event_type: "escalation_resolved" },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": mixed }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.getAllByText("Automated:", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Instructor action:", { exact: false }).length).toBeGreaterThan(0);
  });

  it("BUG-188: does not invent a vote count from an agreement field with unknown provenance", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.queryByText(/grading passes agreed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.67/)).not.toBeInTheDocument();
    expect(screen.queryByText("semantic_grading")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Semantic criterion review/).length).toBeGreaterThan(0);
  });

  it("shows an empty state honestly, not a blank table, and announces it", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": { items: [], total: 0, page: 1, page_size: 25 } }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("No audit events match this filter.")).length).toBe(2);
    expect(await screen.findByText("No audit events match this filter.", { selector: '[role="status"]' })).toBeInTheDocument();
  });

  it("BUG-188: opens with an instructor explanation and keeps exact data in a closed technical disclosure", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": PAGE, "/audit/42": DETAIL }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(`Audit entry #42`)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "What this records" })).toBeInTheDocument();
    expect(screen.getByText(/completed an AI-assisted review step/)).toBeInTheDocument();
    const technical = screen.getByText("Technical record for reproducibility").closest("details");
    expect(technical).not.toHaveAttribute("open");
    expect(await screen.findByText(/agreement_score:\s*0\.667/)).toBeInTheDocument();
    expect(await screen.findByText(/input_hash:\s*abc123/)).toBeInTheDocument();
    expect(screen.getByText(/"prompt_type": "semantic_grading"/)).toBeInTheDocument();
    expect(screen.queryByText(/uv run python -m scripts\.replay_call/)).not.toBeInTheDocument();
    expect(screen.queryByText(/can reproduce the record/i)).not.toBeInTheDocument();
    expect(screen.getByText(/use it in a reproduction workflow/i)).toBeInTheDocument();
  });

  it("BUG-188: keeps override confidence exact in technical data without calling it grader agreement", async () => {
    const override = {
      ...PAGE.items[0],
      id: 43,
      event_type: "flag_overridden",
      prompt_type: null,
      prompt_version: null,
      agreement_score: 0.91,
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": { items: [override], total: 1, page: 1, page_size: 25 },
        "/audit/43": {
          ...override,
          input_hash: null,
          payload: { flag_id: 19, confidence: 0.91, instructor_id: 1 },
        },
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("Flag overridden");
    expect(screen.queryByText(/grading passes|0\.91/)).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(/agreement_score:\s*0\.91/)).toBeInTheDocument();
    expect(screen.queryByText(/grading passes/)).not.toBeInTheDocument();
  });

  it("BUG-022: shows the manuscript's filename in the detail drawer, but not in the list rows (which stay light at volume)", async () => {
    const detailWithFilename = { ...DETAIL, manuscript_original_filename: "Chapter1-3_FinalDefense.pdf" };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({ "/audit": PAGE, "/audit/42": detailWithFilename }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.queryByText("Chapter1-3_FinalDefense.pdf")).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText("Chapter1-3_FinalDefense.pdf")).toBeInTheDocument();
  });

  it("pre-filters by check_run_id when arriving from the report's audit-trail link", async () => {
    const fetchMock = stubFetchByPath({ "/audit": PAGE });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AuditLogPage />, { route: "/audit?check_run_id=7", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.getByText("check run 7")).toBeInTheDocument();
    const calledUrl = fetchMock.mock.calls[0][0] as string;
    expect(calledUrl).toContain("check_run_id=7");
  });

  it("marks the active filter with aria-pressed, not color alone (WCAG 4.1.2)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "AI calls" })).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(screen.getByRole("button", { name: "AI calls" }));
    expect(screen.getByRole("button", { name: "AI calls" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "false");
  });

  it("retains the existing criterion-judgment filter URL and exact event query after relabelling", async () => {
    const fetchMock = stubFetchByPath({ "/audit": PAGE });
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<AuditLogPage />, { route: "/audit?check_run_id=7&filter=Escalations", path: "/audit" });
    await screen.findAllByText("Criterion judgment recorded");
    expect(screen.getByRole("button", { name: "Criterion judgments" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "false");
    expect(new URL(String(fetchMock.mock.calls[0][0]), "http://localhost").searchParams.get("event_type")).toBe("escalation_resolved");
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.click(screen.getByRole("button", { name: "Criterion judgments" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Criterion judgments" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.queryByRole("button", { name: "Escalations" })).not.toBeInTheDocument();
  });

  it("focuses and titles the page on arrival (route focus management, previously missing entirely)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(document.title).toBe("Audit log - VERIDICAL");
  });

  it("the mobile and desktop list regions resolve a real aria-labelledby target (UX review finding: the id didn't exist)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    const heading = document.getElementById("audit-log-heading");
    expect(heading).not.toBeNull();
    expect(heading?.textContent).toBe("Audit");
  });

  it("BUG (UX review finding): the Detail modal shows a real error and retry, never stays stuck on 'Loading' forever", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": PAGE,
        "/audit/42": new Response(
          JSON.stringify({ error: { code: "not_found", message: "No audit entry 42." } }),
          { status: 404 },
        ),
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("No audit entry 42.");
    expect(screen.queryByText("Loading audit entry.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry audit entry" })).toBeInTheDocument();
  });

  it("BUG (UX review finding): Previous/Next disabling itself no longer strands focus on <body> — it lands on the page indicator", async () => {
    const PAGE_MULTI = { ...PAGE, page_size: 1, total: 2 };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE_MULTI }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Page 2 of 2");
    expect(document.activeElement?.textContent).toBe("Page 2 of 2");
  });

  it("labels rubric-parse and model-exhausted events instead of showing the raw event_type string (same bug class as the flag_overridden fix)", async () => {
    const rare = {
      items: [
        { ...PAGE.items[0], id: 50, event_type: "rubric_parse_attempt" },
        { ...PAGE.items[0], id: 51, event_type: "llm_model_exhausted" },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": rare }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("Rubric parse attempt")).length).toBe(2);
    expect(screen.getAllByText("AI model exhausted, failed over").length).toBe(2);
    expect(screen.queryAllByText("rubric_parse_attempt")).toHaveLength(0);
    expect(screen.queryAllByText("llm_model_exhausted")).toHaveLength(0);
  });

  it("BUG-188: labels current workflow events and humanizes future identifiers instead of exposing raw enums", async () => {
    const workflowEvents = {
      items: [
        { ...PAGE.items[0], id: 60, event_type: "citation_source_confirmed" },
        { ...PAGE.items[0], id: 61, event_type: "check_run_cancel_requested" },
        { ...PAGE.items[0], id: 62, event_type: "check_run_cancelled" },
        { ...PAGE.items[0], id: 63, event_type: "manuscript_ingestion_failure_dismissed" },
        { ...PAGE.items[0], id: 64, event_type: "future_review_event" },
      ],
      total: 5,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": workflowEvents }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });

    expect((await screen.findAllByText("Citation source confirmed")).length).toBe(2);
    expect(screen.getAllByText("Check cancellation requested").length).toBe(2);
    expect(screen.getAllByText("Check cancelled").length).toBe(2);
    expect(screen.getAllByText("Failed upload removed from Review Desk").length).toBe(2);
    expect(screen.getAllByText("Future review event").length).toBe(2);
    expect(screen.queryByText(/citation_source_confirmed|future_review_event/)).not.toBeInTheDocument();
  });

  it("still collapses consecutive automated rows that share one execution mode into one group (V-056 grouping, unchanged by BUG-219)", async () => {
    const sameMode = {
      items: [
        { ...PAGE.items[0], id: 70, llm_execution_mode: "fake" as const },
        { ...PAGE.items[0], id: 71, llm_execution_mode: "fake" as const },
        { ...PAGE.items[0], id: 72, llm_execution_mode: "fake" as const },
      ],
      total: 3,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": sameMode }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    // Only the desktop toggle is literally named "Show all" -- the mobile
    // one is an aria-hidden +/- glyph inside a button whose accessible
    // name is the group's own summary text, not this fixed string.
    expect((await screen.findAllByRole("button", { name: "Show all" })).length).toBe(1);
    expect(screen.queryAllByRole("button", { name: "Detail" })).toHaveLength(0);

    // Shared expand state -- toggling the desktop control expands both.
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(await screen.findAllByRole("button", { name: "Detail" })).toHaveLength(6); // 3 rows x (mobile + desktop)
  });

  it("BUG-219: never collapses a fake-mode row and a real-mode row into one group, even when adjacent with the same event_type and manuscript", async () => {
    const mixedMode = {
      items: [
        { ...PAGE.items[0], id: 80, llm_execution_mode: "fake" as const },
        { ...PAGE.items[0], id: 81, llm_execution_mode: "real" as const },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": mixedMode }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    // Two ungrouped single rows, each with its own Detail button -- never
    // one "2 x ..." summary that would have to pick just one mode to state.
    expect((await screen.findAllByRole("button", { name: "Detail" })).length).toBe(4); // 2 rows x (mobile + desktop)
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
  });

  it("BUG-219: tags a fake-mode row 'Test mode' and an unknown-mode row 'Mode unknown', but a real-mode row gets no tag at all", async () => {
    const threeModes = {
      items: [
        { ...PAGE.items[0], id: 90, llm_execution_mode: "fake" as const },
        { ...PAGE.items[0], id: 91, llm_execution_mode: "unknown" as const },
        { ...PAGE.items[0], id: 92, llm_execution_mode: "real" as const },
      ],
      total: 3,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": threeModes }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    expect((await screen.findAllByText("Test mode")).length).toBe(2); // mobile + desktop
    expect(screen.getAllByText("Mode unknown").length).toBe(2);
    // "AI call" itself renders 3x2 = 6 times (once per row per view) --
    // asserting there are only 4 mode tags total is what proves the third
    // (real-mode) row got none, without needing a query that can express
    // "absent on exactly one of three rows."
    expect(screen.queryAllByText("Test mode").length + screen.queryAllByText("Mode unknown").length).toBe(4);
  });

  it("BUG-219: a non-LLM event never carries a mode tag, even defensively (the frontend's own gate, not just trust in the backend's)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("Criterion judgment recorded");
    expect(screen.queryByText("Test mode")).not.toBeInTheDocument();
    expect(screen.queryByText("Mode unknown")).not.toBeInTheDocument();
  });

  it("BUG-219: the Detail explanation names test-mode/real/unknown honestly instead of always claiming 'completed an AI-assisted review step' with no qualifier", async () => {
    const fakeRow = { ...PAGE.items[0], id: 95, llm_execution_mode: "fake" as const };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": { items: [fakeRow], total: 1, page: 1, page_size: 25 },
        "/audit/95": { ...fakeRow, input_hash: null, payload: { prompt_type: "semantic_grading", response: {} } },
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    fireEvent.click((await screen.findAllByRole("button", { name: "Detail" }))[0]);
    expect(await screen.findByText(/using its own fixture responses, not a real external AI model/)).toBeInTheDocument();
    expect(screen.getByText(/zero-budget default for development and demo use/)).toBeInTheDocument();
  });

  it("BUG-219 (backend review P1): a fixture or unknown-mode row's label and explanation never contain the literal phrases 'AI call' or 'AI-assisted' -- a real-mode row still does, per the ticket's own regression-test text", async () => {
    const threeModes = {
      items: [
        { ...PAGE.items[0], id: 97, llm_execution_mode: "fake" as const },
        { ...PAGE.items[0], id: 98, llm_execution_mode: "unknown" as const },
        { ...PAGE.items[0], id: 99, llm_execution_mode: "real" as const },
      ],
      total: 3,
      page: 1,
      page_size: 25,
    };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": threeModes,
        "/audit/97": { ...threeModes.items[0], input_hash: null, payload: {} },
        "/audit/98": { ...threeModes.items[1], input_hash: null, payload: {} },
        "/audit/99": { ...threeModes.items[2], input_hash: null, payload: {} },
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("Test mode");

    // List: the bold label itself, not just the adjacent tag.
    expect(screen.getAllByText("Test-mode call").length).toBe(2); // mobile + desktop
    expect(screen.getAllByText("Automated review step").length).toBe(2);
    expect(screen.getAllByText("AI call").length).toBe(2); // the real-mode row only

    const detailButtons = screen.getAllByRole("button", { name: "Detail" });
    for (const [index, mode] of [
      [0, "fake"],
      [1, "unknown"],
      [2, "real"],
    ] as const) {
      fireEvent.click(detailButtons[index]);
      const explanation = await screen.findByRole("heading", { name: "What this records" });
      const text = explanation.parentElement?.textContent ?? "";
      expect(text.includes("AI call")).toBe(false); // never, in any mode
      // Only a confirmed real-model record affirmatively claims "AI-assisted" --
      // the ticket's own wording for what that phrase must be reserved for.
      expect(text.includes("AI-assisted")).toBe(mode === "real");
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
    }
  });

  it("BUG-219: an unknown-mode row's Detail explanation says the mode was never recorded, not that it was real or fake", async () => {
    const unknownRow = { ...PAGE.items[0], id: 96, llm_execution_mode: "unknown" as const };
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/audit": { items: [unknownRow], total: 1, page: 1, page_size: 25 },
        "/audit/96": { ...unknownRow, input_hash: null, payload: { prompt_type: "semantic_grading", response: {} } },
      }),
    );
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    fireEvent.click((await screen.findAllByRole("button", { name: "Detail" }))[0]);
    expect(
      await screen.findByText(/predates VERIDICAL's AI-mode tracking, so whether it used a real or test-mode AI model was never recorded/),
    ).toBeInTheDocument();
  });

  it("BUG-219: names VERIDICAL, not an unscoped absolute, as the actor disallowing edits/deletes, on both the page intro and the Detail modal", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/audit": PAGE, "/audit/42": DETAIL }));
    renderWithProviders(<AuditLogPage />, { route: "/audit", path: "/audit" });
    await screen.findAllByText("AI call");
    expect(
      screen.getByText("Trace system activity and instructor actions. VERIDICAL does not allow an audit entry to be edited or deleted."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Audit entries cannot be edited or deleted\.?$/)).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Detail" })[0]);
    expect(await screen.findByText(/VERIDICAL does not allow this entry to be edited or deleted/)).toBeInTheDocument();
    expect(screen.getByText(/A database trigger rejects UPDATE and DELETE while enabled/)).toBeInTheDocument();
    expect(screen.getByText(/Privileged database access can bypass or disable this safeguard/)).toBeInTheDocument();
    expect(screen.queryByText(/^This entry is immutable/)).not.toBeInTheDocument();
  });
});
