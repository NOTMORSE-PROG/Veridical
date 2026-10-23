import { fireEvent, render, screen, within } from "@testing-library/react";
import { StrictMode, useRef, useState } from "react";
import { describe, expect, it } from "vitest";
import { Dialog } from "./Dialog";

function Fixture({ customReturn = true }: { customReturn?: boolean }) {
  const [open, setOpen] = useState(false);
  const [preferHeading, setPreferHeading] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  return <div id="root">
    <h1 ref={headingRef} tabIndex={-1}>Continuation</h1>
    <button onClick={() => setOpen(true)}>Open control dialog</button>
    {open && <Dialog title="Control dialog" onClose={() => setOpen(false)}
      returnFocusTarget={customReturn ? () => preferHeading ? headingRef.current : null : undefined}
      fallbackFocusRef={customReturn ? headingRef : undefined}>
      <button onClick={() => setPreferHeading(true)}>Choose continuation</button>
    </Dialog>}
  </div>;
}

function open(customReturn = true) {
  render(<StrictMode><Fixture customReturn={customReturn} /></StrictMode>);
  const trigger = screen.getByRole("button", { name: "Open control dialog" });
  trigger.focus(); fireEvent.click(trigger);
  expect(document.getElementById("root")).toHaveAttribute("inert");
  return trigger;
}

describe("Dialog return focus", () => {
  it("includes the native closed disclosure in the keyboard cycle without entering its hidden content", () => {
    render(<Dialog title="Recorded event" initialFocusTitle onClose={() => {}}>
      <details><summary>Technical record</summary><button>Hidden until expanded</button></details>
      <button hidden>Hidden control</button>
    </Dialog>);
    const title = screen.getByRole("heading", { name: "Recorded event" });
    expect(title).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByText("Technical record")).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Close" })).toHaveFocus();
    screen.getByText("Technical record").parentElement!.setAttribute("open", "");
    title.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "Hidden until expanded" })).toHaveFocus();
  });

  it("skips disabled fieldset controls while retaining the enabled first-legend control", () => {
    render(<Dialog title="Recorded event" initialFocusTitle onClose={() => {}}>
      <fieldset disabled><legend><button>Available legend action</button></legend><button>Disabled by fieldset</button></fieldset>
    </Dialog>);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "Available legend action" })).toHaveFocus();
  });

  it("preserves ordinary captured-trigger return without any caller options", () => {
    const trigger = open(false);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(trigger).toHaveFocus();
    expect(document.getElementById("root")).not.toHaveAttribute("inert");
  });

  it.each(["disabled", "detached", "hidden", "inert", "aria-hidden", "css-hidden"])(
    "uses the persistent fallback after the trigger becomes %s",
    (reason) => {
      const trigger = open();
      if (reason === "disabled") (trigger as HTMLButtonElement).disabled = true;
      else if (reason === "detached") trigger.remove();
      else if (reason === "css-hidden") trigger.style.display = "none";
      else trigger.setAttribute(reason, reason === "aria-hidden" ? "true" : "");
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
      expect(screen.getByRole("heading", { name: "Continuation" })).toHaveFocus();
      expect(document.getElementById("root")).not.toHaveAttribute("inert");
    },
  );

  it("reads the latest preferred target once at close without refocusing on resolver updates", () => {
    open();
    const choose = within(screen.getByRole("dialog")).getByRole("button", { name: "Choose continuation" });
    choose.focus(); fireEvent.click(choose);
    expect(choose).toHaveFocus();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("heading", { name: "Continuation" })).toHaveFocus();
  });

  it.each([false, true])("preserves newer usable outside focus before preferred=%s or fallback restoration", (prefer) => {
    open();
    if (prefer) fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Choose continuation" }));
    const outside = document.createElement("button");
    outside.textContent = "New route or dialog focus";
    document.body.append(outside);
    try {
      outside.focus();
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
      expect(outside).toHaveFocus();
    } finally { outside.remove(); }
  });
});
