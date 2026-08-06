import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";
import { renderWithProviders, stubFetchByPath } from "../test/renderWithProviders";
import { SignInPage } from "./SignIn";

const SIGNED_OUT = new Response(JSON.stringify({ error: { code: "unauthenticated", message: "x" } }), {
  status: 401,
});

describe("SignInPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows an inline validation message on empty submit, never a native alert (custom-everything rule)", async () => {
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent("Enter your email address.");
    expect(summary).toHaveTextContent("Enter your password.");
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it("moves focus to the error summary on empty submit (SPA focus-management rule)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const summary = await screen.findByRole("alert");
    await waitFor(() => expect(document.activeElement).toBe(summary));
  });

  it("shows a combined, non-field-specific message on wrong credentials (no user-enumeration)", async () => {
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

    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "prof@tip.edu.ph" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const summary = await screen.findByRole("alert");
    expect(summary).toHaveTextContent(
      "We could not sign you in with those details. Check your email and password and try again.",
    );
    expect(screen.getByLabelText("Email address")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true");
    // The 401 path deliberately has no per-field message, so aria-describedby
    // must not point at a nonexistent id (WCAG 4.1.2, found live in review).
    expect(screen.getByLabelText("Email address")).not.toHaveAttribute("aria-describedby");
    expect(screen.getByLabelText("Password")).not.toHaveAttribute("aria-describedby");
  });

  it("toggles password visibility via a labeled custom control, not a native affordance", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));

    const passwordInput = screen.getByLabelText("Password");
    expect(passwordInput).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Show password" }));
    expect(passwordInput).toHaveAttribute("type", "text");
    expect(screen.getByRole("button", { name: "Hide password" })).toBeInTheDocument();
  });

  it("the form runs noValidate (custom validation owns the UX, not the browser)", async () => {
    vi.stubGlobal("fetch", stubFetchByPath({ "/auth/me": SIGNED_OUT }));
    const { container } = renderWithProviders(<SignInPage />);
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));
    expect(container.querySelector("form")).toHaveAttribute("novalidate");
  });

  it("BUG-011: a successful sign-in honors the deep-link that sent the visitor here, not a hardcoded /dashboard", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetchByPath({
        "/auth/me": SIGNED_OUT,
        "/auth/login": { id: 1, email: "prof@tip.edu.ph", display_name: "Prof" },
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={[
            { pathname: "/signin", state: { from: { pathname: "/report/42" } } },
          ]}
        >
          <Routes>
            <Route path="/signin" element={<SignInPage />} />
            <Route path="/report/:id" element={<div>Report page</div>} />
            <Route path="/dashboard" element={<div>Dashboard page</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => screen.getByRole("button", { name: "Sign in" }));

    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "prof@tip.edu.ph" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Report page")).toBeInTheDocument();
    expect(screen.queryByText("Dashboard page")).not.toBeInTheDocument();
  });
});
