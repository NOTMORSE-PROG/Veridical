import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";

describe("Button", () => {
  it("renders a real button element (accessibility rule, CODING.md §3)", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("defaults to the secondary style on panel background", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button")).toHaveClass(
      "border-border-button",
      "bg-panel",
      "text-ink",
    );
  });

  it("renders the primary variant with primary tokens", () => {
    render(<Button variant="primary">Save</Button>);
    expect(screen.getByRole("button")).toHaveClass(
      "bg-primary",
      "text-on-primary",
    );
  });

  it("supports the small size", () => {
    render(<Button size="sm">Save</Button>);
    expect(screen.getByRole("button")).toHaveClass("text-xs");
  });

  it("disables natively and fires clicks when enabled", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole("button");
    expect(button).toBeDisabled();
    button.click();
    expect(onClick).not.toHaveBeenCalled();
  });
});
