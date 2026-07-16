import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "./Modal";

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
});
