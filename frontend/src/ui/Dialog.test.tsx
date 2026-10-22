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
