import { act, fireEvent, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithReviewProviders } from "../test/renderWithProviders";
import { ME_QUERY_KEY } from "../auth/useAuth";
import { useSelectedSourceReveal } from "./useSelectedSourceReveal";

afterEach(() => vi.restoreAllMocks());
function setup({ delayed = false, hidden = false, parentScroll = false } = {}) {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const scroll = vi.fn();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    return { top: this.hasAttribute("data-source-record-id") ? 1200 : 100, bottom: this.hasAttribute("data-source-record-id") ? 1500 : 600 } as DOMRect;
  });
  let load!: () => void; let refresh!: () => void; let client!: QueryClient;
  function Harness() {
    client = useQueryClient();
    const [loaded, setLoaded] = useState(!delayed); load = () => setLoaded(true);
    const [version, setVersion] = useState(0); refresh = () => setVersion(x => x + 1);
    const [search, setSearch] = useState("queue");
    const source = useRef<HTMLDivElement>(null);
    const reveal = useSelectedSourceReveal({ runId: 5, search, requestedId: null,
      resolvedId: search === "finding41" ? 41 : 42, visible: !hidden, source });
    return <><button onClick={() => { reveal.arm("finding41", 41); setSearch("finding41"); }}>Select 41</button>
      <button onClick={() => { reveal.arm("finding42", 42); setSearch("finding42"); }}>Select 42</button>
      <button>Other work</button><div className="source-pane" ref={node => { source.current = node; if (node && parentScroll) node.scrollTo = scroll; }} data-version={version}><div className="signal-document-scroll--docx" style={{ overflowY: parentScroll ? "visible" : "auto" }} ref={node => { if (node && !parentScroll) node.scrollTo = scroll; }}>
        {loaded && <button data-source-record-id={search === "finding41" ? 41 : 42}>Actual selected source</button>}
      </div></div></>;
  }
  renderWithReviewProviders(<Harness />);
  const action = screen.getByRole("button", { name: "Select 41" }); action.focus(); fireEvent.click(action);
  return { scroll, load, refresh, client, action };
}

describe("owned selected source reveal", () => {
  it("scrolls only the actual source root once while retaining review focus", () => {
    const c = setup();
    expect(c.scroll).toHaveBeenCalledTimes(1);
    expect(c.scroll.mock.instances[0]).toHaveClass("signal-document-scroll--docx");
    expect(c.scroll).toHaveBeenCalledWith({ top: 1100, behavior: "auto" });
    expect(c.action).toHaveFocus();
    act(c.refresh); expect(c.scroll).toHaveBeenCalledTimes(1);
  });
  it("waits for source data without transferring focus", () => {
    const c = setup({ delayed: true }); expect(c.scroll).not.toHaveBeenCalled();
    act(c.load); expect(c.scroll).toHaveBeenCalledTimes(1); expect(c.action).toHaveFocus();
  });
  it("uses the actual outer manuscript scroller in the single-pane layout", () => {
    const c = setup({ parentScroll: true });
    expect(c.scroll).toHaveBeenCalledTimes(1);
    expect(c.scroll.mock.instances[0]).toHaveClass("source-pane");
    expect(c.action).toHaveFocus();
  });
  it.each(["wheel", "pointer", "key", "focus", "blur", "auth", "same query account change", "same query sign out"])("retires a delayed placement after %s", (intent) => {
    const c = setup({ delayed: true });
    if (intent === "wheel") window.dispatchEvent(new Event("wheel"));
    if (intent === "pointer") fireEvent.pointerDown(screen.getByRole("button", { name: "Other work" }));
    if (intent === "key") fireEvent.keyDown(document, { key: "Tab" });
    if (intent === "focus") screen.getByRole("button", { name: "Other work" }).focus();
    if (intent === "blur") window.dispatchEvent(new Event("blur"));
    if (intent === "auth") act(() => { c.client.removeQueries({ queryKey: ME_QUERY_KEY, exact: true }); c.client.setQueryData(ME_QUERY_KEY, { id: 1 }); });
    if (intent === "same query account change") act(() => { c.client.setQueryData(ME_QUERY_KEY, { id: 2 }); });
    if (intent === "same query sign out") act(() => { c.client.setQueryData(ME_QUERY_KEY, null); });
    act(c.load); act(c.refresh); expect(c.scroll).not.toHaveBeenCalled();
  });
  it("does not scroll the hidden source", () => {
    const c = setup({ hidden: true }); expect(c.scroll).not.toHaveBeenCalled(); expect(c.action).toHaveFocus();
  });
  it("replaces an absent source attempt with the actual next record", () => {
    const c = setup({ delayed: true });
    const next = screen.getByRole("button", { name: "Select 42" }); next.focus(); fireEvent.click(next);
    act(c.load); expect(c.scroll).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Actual selected source" })).toHaveAttribute("data-source-record-id", "42");
    expect(next).toHaveFocus();
  });
});
