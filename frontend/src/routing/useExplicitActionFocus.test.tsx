import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { useExplicitActionFocus } from "./useExplicitActionFocus";

afterEach(() => vi.restoreAllMocks());

function setup({ accepted = true, interrupt = () => {} } = {}) {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  let refresh!: () => void;
  function Harness() {
    const [editing, setEditing] = useState(false);
    const [, tick] = useState(0);
    refresh = () => tick(n => n + 1);
    const editor = useRef<HTMLTextAreaElement>(null);
    const follow = useExplicitActionFocus(() => editor.current);
    return <>{!editing && <button onClick={event => follow(event.currentTarget, () => {
      interrupt();
      if (accepted) setEditing(true);
    })}>Use this draft</button>}<textarea ref={editor} aria-label="Retained reason" /><button>Other task</button></>;
  }
  const rendered = render(<Harness />);
  const trigger = screen.getByRole("button", { name: "Use this draft" });
  return { ...rendered, trigger, refresh: () => refresh() };
}

it("continues an owned rebind to its reason when its trigger disappears", () => {
  const { trigger } = setup(); trigger.focus(); fireEvent.click(trigger);
  expect(trigger.isConnected).toBe(false);
  expect(screen.getByRole("textbox")).toHaveFocus();
});

it("does not transfer focus for a guard-rejected action on a later background render", () => {
  const { trigger, refresh } = setup({ accepted: false });
  trigger.focus(); fireEvent.click(trigger);
  expect(trigger).toHaveFocus();
  act(refresh);
  expect(trigger).toHaveFocus();
});

it("does not claim focus for a programmatic unfocused action", () => {
  const { trigger } = setup(); const other = screen.getByRole("button", { name: "Other task" });
  other.focus(); fireEvent.click(trigger);
  expect(other).toHaveFocus();
});

it.each(["Tab", "Space", "pointer", "wheel", "blur", "other focus"])(
  "preserves newer %s intent before the continuation commits", intent => {
    const { trigger } = setup({ interrupt: () => {
      if (intent === "Tab" || intent === "Space") fireEvent.keyDown(document, { key: intent === "Tab" ? "Tab" : " " });
      if (intent === "pointer") fireEvent.pointerDown(document.body);
      if (intent === "wheel") fireEvent.wheel(window);
      if (intent === "blur") fireEvent(window, new Event("blur"));
      if (intent === "other focus") screen.getByRole("button", { name: "Other task" }).focus();
    } });
    trigger.focus(); fireEvent.click(trigger);
    expect(screen.getByRole("textbox")).not.toHaveFocus();
    if (intent === "other focus") expect(screen.getByRole("button", { name: "Other task" })).toHaveFocus();
  },
);
