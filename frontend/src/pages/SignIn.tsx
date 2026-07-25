// Screen 4a — Sign in (F9.1). Custom-everything rule (DESIGN.md §2): form
// runs noValidate, validation renders as a styled inline message, never a
// native bubble or alert().
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useLogin, useMe } from "../auth/useAuth";

interface LocationState {
  from?: { pathname: string };
}

export function SignInPage() {
  const { data: me, isPending: mePending } = useMe();
  const location = useLocation();
  const navigate = useNavigate();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  if (!mePending && me) {
    const from = (location.state as LocationState | null)?.from?.pathname ?? "/dashboard";
    return <Navigate to={from} replace />;
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setFieldError(null);
    if (!email.trim() || !password) {
      setFieldError("Enter your email and password.");
      return;
    }
    login.mutate(
      { email: email.trim(), password },
      { onSuccess: () => navigate("/dashboard", { replace: true }) },
    );
  }

  const serverError =
    login.error instanceof ApiError
      ? login.error.message
      : login.error
        ? "Something went wrong. Please try again."
        : null;
  const message = fieldError ?? serverError;

  return (
    <div className="flex min-h-screen items-center justify-center bg-page p-8">
      <div className="flex w-[360px] flex-col items-center gap-3.5">
        <div className="flex items-center gap-2">
          <span className="flex h-[18px] w-[18px] items-center justify-center rounded-tag bg-primary text-2xs font-bold text-on-primary">
            V
          </span>
          <span className="text-xs font-bold tracking-header">VERIDICAL</span>
        </div>
        <p className="text-center text-xs text-ink-faint">
          Quality &amp; integrity assurance for capstone manuscripts
        </p>
        <form
          noValidate
          onSubmit={handleSubmit}
          className="flex w-full flex-col gap-2.5 rounded-panel border border-border bg-panel p-4"
        >
          <label className="flex flex-col gap-1">
            <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
              Email
            </span>
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="name@tip.edu.ph"
              className="rounded-control border border-border-input px-2.5 py-1.5 text-base text-ink placeholder:text-placeholder focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-2xs font-semibold tracking-header text-table-head uppercase">
              Password
            </span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
              className="rounded-control border border-border-input px-2.5 py-1.5 text-base text-ink focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
            />
          </label>
          {message && (
            <p role="alert" className="text-xs font-medium text-status-bad-text">
              {message}
            </p>
          )}
          <button
            type="submit"
            disabled={login.isPending}
            className="mt-1 flex items-center justify-center rounded-control border border-primary bg-primary px-3.5 py-1.5 text-base font-medium text-on-primary disabled:opacity-60"
          >
            {login.isPending ? "Signing in…" : "Sign in"}
          </button>
          <p className="text-center text-xs text-ink-faint">
            Forgot password? Contact your administrator.
          </p>
        </form>
        <p className="text-center text-xs text-ink-faint">
          Role-based access: instructor and student accounts route to their own workspace. TLS +
          encrypted storage.
        </p>
      </div>
    </div>
  );
}
