import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlagOut } from "../api/types";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { FlagReviewActions } from "./FlagReviewActions";
import { useFlag } from "./useFlag";

const finding = (id: number): FlagOut => ({ assessment_revision: 0, id, check_result_id: 9, check_run_id: 5,
  manuscript_group_label: "Source control", check_kind: "internal_agreement", criterion_text: null, severity: "med",
  confidence: null, evidence_excerpt: "Saved statement.", page_anchor: "page 1", annotation: null, overridden: false,
  override_reason: null, ai_verdict_summary: "possible_mismatch", ai_reasoning: null, llm_mode: "real",
  is_passage_level: false, passage_pair: null, first_upload_context: false, evidence_unavailable: false,
  citation_source_key: null, confirmed_citation_source: false });

function Harness() {
  const [id, select] = useState(7);
  const [visible, setVisible] = useState(true);
  const query = useFlag(id);
  const navigate = useNavigate();
  const client = useQueryClient();
  return <><button onClick={() => select(current => current === 7 ? 8 : 7)}>Other finding</button>
    <button onClick={() => navigate("?source=page_text")}>Change source presentation</button>
    <button onClick={() => client.setQueryData(ME_QUERY_KEY, { id: 2 })}>Other instructor</button>
    <button onClick={() => client.setQueryData(ME_QUERY_KEY, { id: 1 })}>First instructor</button>
    <button onClick={() => { client.setQueryData(ME_QUERY_KEY, { id: 2 }); client.setQueryData(ME_QUERY_KEY, { id: 1 }); }}>Batched account round trip</button>
    <button onClick={() => { client.setQueryData(ME_QUERY_KEY, null); client.setQueryData(ME_QUERY_KEY, { id: 1 }); }}>Batched access loss</button>
    <button onClick={() => setVisible(current => !current)}>Toggle review</button>
    <button onClick={() => { client.removeQueries({ queryKey: ME_QUERY_KEY, exact: true }); client.setQueryData(ME_QUERY_KEY, { id: 1 }); }}>New login</button>
    {visible && query.data && <FlagReviewActions flag={query.data} />}</>;
}
function setup(handlers: Record<string, unknown> = {}) {
  vi.stubGlobal("fetch", stubFetchByPath({ "/flags/7": finding(7), "/flags/8": finding(8), ...handlers }));
  return renderWithProviders(<Harness />, { route: "/flags/7", authInstructorId: 1 });
}
async function annotation() {
  await screen.findByRole("heading", { name: "Resolve only after checking the evidence" });
  const summary = screen.getByText("Instructor annotation", { selector: "summary" });
  if (!(summary.parentElement as HTMLDetailsElement).open) fireEvent.click(summary);
  return screen.getByRole("textbox", { name: "Annotation" });
}
afterEach(() => vi.unstubAllGlobals());

describe("one private annotation draft per authenticated finding", () => {
  it("retains its draft through source presentation and finding round trips without leaking into the other finding", async () => {
    setup();
    fireEvent.change(await annotation(), { target: { value: "Unsaved source-specific note A." } });
    fireEvent.click(screen.getByRole("button", { name: "Change source presentation" }));
    expect(await annotation()).toHaveValue("Unsaved source-specific note A.");
    fireEvent.click(screen.getByRole("button", { name: "Other finding" }));
    await waitFor(async () => expect(await annotation()).toHaveValue(""));
    fireEvent.change(await annotation(), { target: { value: "Independent note B." } });
    fireEvent.click(screen.getByRole("button", { name: "Other finding" }));
    await waitFor(async () => expect(await annotation()).toHaveValue("Unsaved source-specific note A."));
    fireEvent.click(screen.getByRole("button", { name: "Discard unsaved annotation" }));
    expect(await annotation()).toHaveValue("");
  });
  it.each(["Other instructor", "New login"])("retires the private draft across %s", async action => {
    setup();
    fireEvent.change(await annotation(), { target: { value: "Private first lifetime note." } });
    fireEvent.click(screen.getByRole("button", { name: action }));
    await waitFor(async () => expect(await annotation()).toHaveValue(""));
    if (action === "Other instructor") {
      fireEvent.click(screen.getByRole("button", { name: "First instructor" }));
      await waitFor(async () => expect(await annotation()).toHaveValue(""));
    }
  });
  it.each([true, false].flatMap(closed => ["Batched account round trip", "Batched access loss"].map(action => ({ closed, action }))))("retires a $action draft even when review is closed=$closed", async ({ closed, action }) => {
    setup();
    fireEvent.change(await annotation(), { target: { value: "Retire across intermediate authentication changes." } });
    if (closed) fireEvent.click(screen.getByRole("button", { name: "Toggle review" }));
    fireEvent.click(screen.getByRole("button", { name: action }));
    if (closed) fireEvent.click(screen.getByRole("button", { name: "Toggle review" }));
    await waitFor(async () => expect(await annotation()).toHaveValue(""));
  });
  it.each(["wrong id", "wrong run", "wrong annotation"])("withholds success and retains the draft after a %s acknowledgement", async kind => {
    const note = "Keep until confirmed.";
    setup({ "/flags/7/annotate": { ...finding(kind === "wrong id" ? 8 : 7), check_run_id: kind === "wrong run" ? 6 : 5, annotation: kind === "wrong annotation" ? "Different saved note." : note } });
    fireEvent.change(await annotation(), { target: { value: note } });
    fireEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    await screen.findByText(/response did not confirm this annotation/);
    expect(screen.queryByText("Annotation saved.")).not.toBeInTheDocument();
    expect(await annotation()).toHaveValue(note);
    expect(screen.getByText(/Unsaved note kept/)).toBeInTheDocument();
  });
  it("accepts a coherent saved note after a source-only route change", async () => {
    const note = "Checked the saved source.";
    setup({ "/flags/7/annotate": { ...finding(7), annotation: note } });
    fireEvent.change(await annotation(), { target: { value: note } });
    fireEvent.click(screen.getByRole("button", { name: "Change source presentation" }));
    fireEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    await screen.findByText("Annotation saved.");
    expect(screen.queryByText(/Unsaved note kept/)).not.toBeInTheDocument();
  });
  it("does not acknowledge an old finding's late save on a different finding", async () => {
    let release!: (response: Response) => void;
    const held = new Promise<Response>(resolve => { release = resolve; });
    const base = stubFetchByPath({ "/flags/7": finding(7), "/flags/8": finding(8) });
    vi.stubGlobal("fetch", vi.fn(async (input, init) => String(input).endsWith("/flags/7/annotate") ? held : base(input, init)));
    renderWithProviders(<Harness />, { route: "/flags/7", authInstructorId: 1 });
    fireEvent.change(await annotation(), { target: { value: "Late note A." } });
    fireEvent.click(screen.getByRole("button", { name: "Save annotation" }));
    fireEvent.click(screen.getByRole("button", { name: "Other finding" }));
    await waitFor(async () => expect(await annotation()).toHaveValue(""));
    await act(async () => { release(new Response(JSON.stringify({ ...finding(7), annotation: "Late note A." }))); await held; });
    expect(screen.queryByText("Annotation saved.")).not.toBeInTheDocument();
    expect(await annotation()).toHaveValue("");
  });
});
