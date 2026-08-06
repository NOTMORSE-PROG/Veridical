import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Modal, ModalBackdrop } from "./Modal";

function renderInRoot(ui: React.ReactElement) {
  // ModalBackdrop portals to document.body and inerts #root -- both need
  // a real #root element in the DOM to target (BUG-020's own regression
  // test request: component-level, not per-screen).
  const root = document.createElement("div");
  root.id = "root";
  document.body.appendChild(root);
  const utils = render(ui, { container: root });
  return { ...utils, root };
}

afterEach(() => {
  document.getElementById("root")?.remove();
});

describe("Modal", () => {
  it("renders a dialog with its title", () => {
    render(<Modal title="Upload required format" />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Upload required format")).toBeInTheDocument();
  });

  it("fires onClose from the close button", () => {
    const onClose = vi.fn();
    render(<Modal title="t" onClose={onClose} />);
    screen.getByRole("button", { name: "Close" }).click();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("omits the close button when onClose is absent", () => {
    render(<Modal title="t" />);
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  });

  it("has aria-modal and aria-labelledby pointing at the real title (BUG-020)", () => {
    render(<Modal title="Upload required format" />);
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(document.getElementById(labelledBy!)).toHaveTextContent("Upload required format");
  });

  it("moves focus into the dialog on open (BUG-020)", () => {
    render(
      <Modal title="t" footer={<button type="button">First focusable</button>} />,
    );
    expect(document.activeElement).toHaveTextContent("First focusable");
  });

  it("Escape calls onClose (BUG-020)", () => {
    const onClose = vi.fn();
    render(<Modal title="t" onClose={onClose} footer={<button type="button">Go</button>} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Tab wraps from the last focusable element back to the first (BUG-020)", () => {
    render(
      <Modal
        title="t"
        onClose={() => {}}
        footer={
          <>
            <button type="button">Cancel</button>
            <button type="button">Continue</button>
          </>
        }
      />,
    );
    const closeBtn = screen.getByRole("button", { name: "Close" });
    const continueBtn = screen.getByRole("button", { name: "Continue" });
    continueBtn.focus();
    expect(document.activeElement).toBe(continueBtn);
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(closeBtn);
  });

  it("Shift+Tab wraps from the first focusable element to the last (BUG-020)", () => {
    render(
      <Modal
        title="t"
        onClose={() => {}}
        footer={
          <>
            <button type="button">Cancel</button>
            <button type="button">Continue</button>
          </>
        }
      />,
    );
    const closeBtn = screen.getByRole("button", { name: "Close" });
    const continueBtn = screen.getByRole("button", { name: "Continue" });
    closeBtn.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(continueBtn);
  });

  it("restores focus to the trigger element on unmount (BUG-020)", () => {
    const trigger = document.createElement("button");
    trigger.textContent = "Open";
    document.body.appendChild(trigger);
    trigger.focus();

    const { unmount } = render(<Modal title="t" footer={<button type="button">Go</button>} />);
    expect(document.activeElement).not.toBe(trigger);
    unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});

describe("ModalBackdrop", () => {
  it("portals its content to document.body, outside the app root", () => {
    const { root } = renderInRoot(
      <ModalBackdrop>
        <Modal title="t" />
      </ModalBackdrop>,
    );
    const dialog = screen.getByRole("dialog");
    expect(root.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
  });

  it("marks the app root inert while open, and clears it on unmount (BUG-020)", () => {
    const { root, unmount } = renderInRoot(
      <ModalBackdrop>
        <Modal title="t" />
      </ModalBackdrop>,
    );
    expect(root.hasAttribute("inert")).toBe(true);
    unmount();
    expect(root.hasAttribute("inert")).toBe(false);
  });
});
