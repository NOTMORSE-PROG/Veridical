import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignInPage } from "./SignIn";

const SIGNED_OUT = new Response(JSON.stringify({ error: { code: "unauthenticated", message: "x" } }), {
  status: 401,
});

describe("SignInPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an inline validation message on empty submit, never a native alert (DESIGN.md §2)", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter your email and password.");
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it("shows the server's generic error on wrong credentials", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": SIGNED_OUT,
        "/auth/login": new Response(
          JSON.stringify({ error: { code: "unauthenticated", message: "Incorrect email or password." } }),
          { status: 401 },
        ),
      }),
    );
    renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));

    fireEvent.change(screen.getByPlaceholderText("name@tip.edu.ph"), {
      target: { value: "prof@tip.edu.ph" },
    });
    fireEvent.change(screen.getByPlaceholderText("••••••••"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
  });

  it("the form runs noValidate (custom validation owns the UX, not the browser)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    const { container } = renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));
    expect(container.querySelector("form")).toHaveAttribute("novalidate");
  });
});
