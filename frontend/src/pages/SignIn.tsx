// Screen 4a — Sign in (F9.1). V-055 reconstruction spec (2026-08-05):
// masthead + real H1 + two-column form/sidebar layout, GOV.UK-style error
// summary, no native validation bubbles or alert() (custom-everything rule).
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useLogin, useMe } from "../auth/useAuth";
import { useRouteFocus } from "../routing/useRouteFocus";

interface LocationState {
  from?: { pathname: string };
}

interface FieldErrors {
  email?: string;
  password?: string;
}

export function SignInPage() {
  const { data: me, isPending: mePending } = useMe();
  const location = useLocation();
  const navigate = useNavigate();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [liveMessage, setLiveMessage] = useState("");
  // Increments on every submit attempt that produces a new error (client
  // validation OR server rejection) — the one signal the focus-move effect
  // needs, since neither fieldErrors/formError alone fires on a validation
  // re-submit with the same message twice in a row.
  const [errorAttempt, setErrorAttempt] = useState(0);

  const emailId = useId();
  const passwordId = useId();
  const emailErrorId = `${emailId}-error`;
  const passwordErrorId = `${passwordId}-error`;
  const summaryRef = useRef<HTMLDivElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useRouteFocus("Sign in - VERIDICAL", headingRef);

  const hasErrors = Boolean(formError || fieldErrors.email || fieldErrors.password);

  useEffect(() => {
    if (errorAttempt > 0) summaryRef.current?.focus();
  }, [errorAttempt]);

  if (!mePending && me) {
    const from = (location.state as LocationState | null)?.from?.pathname ?? "/dashboard";
    return <Navigate to={from} replace />;
  }

  function focusField(field: "email" | "password") {
    (field === "email" ? emailRef : passwordRef).current?.focus();
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const errors: FieldErrors = {};
    if (!email.trim()) errors.email = "Enter your email address.";
    if (!password) errors.password = "Enter your password.";
    setFieldErrors(errors);
    setFormError(null);

    if (errors.email || errors.password) {
      setErrorAttempt((n) => n + 1);
      return;
    }

    setLiveMessage("Signing in, please wait.");
    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: () => {
          setLiveMessage("");
          navigate("/dashboard", { replace: true });
        },
        onError: (err) => {
          setLiveMessage("");
          if (err instanceof ApiError && err.status === 401) {
            setFormError(
              "We could not sign you in with those details. Check your email and password and try again.",
            );
            setFieldErrors({ email: " ", password: " " });
          } else {
            setFormError("Something went wrong on our end. Try again in a moment.");
          }
          setErrorAttempt((n) => n + 1);
        },
      },
    );
  }

  const emailInvalid = Boolean(fieldErrors.email);
  const passwordInvalid = Boolean(fieldErrors.password);

  return (
    <div className="flex min-h-screen flex-col bg-page">
      <a
        href="#signin-form"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-50 focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to sign-in form
      </a>

      <header className="border-b-[3px] border-accent bg-action">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center justify-between px-4 sm:h-16 sm:px-6 lg:px-10">
          <div className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="flex h-7 w-7 items-center justify-center rounded-sm bg-accent text-sm font-bold text-ink sm:h-8 sm:w-8"
            >
              V
            </span>
            <span className="text-base font-bold tracking-header text-on-action sm:text-md">
              VERIDICAL
            </span>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1120px] flex-1 px-4 pt-8 pb-16 sm:px-6 sm:pt-12 lg:px-10">
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,480px)_minmax(0,360px)]">
          <div>
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="text-lg font-bold text-ink outline-none sm:text-xl"
            >
              Sign in to VERIDICAL
            </h1>
            <p className="mt-2 text-base text-ink-secondary">
              Quality and integrity assurance for BSIT capstone manuscripts.
            </p>
            {hasErrors && (
              <div
                ref={summaryRef}
                role="alert"
                aria-live="assertive"
                tabIndex={-1}
                className="mt-6 rounded-md border-2 border-danger p-4"
              >
                <p className="text-md font-bold text-danger">There is a problem</p>
                <ul className="mt-2 list-inside list-disc space-y-1">
                  {formError && <li className="text-sm text-danger">{formError}</li>}
                  {fieldErrors.email && fieldErrors.email.trim() && (
                    <li className="text-sm">
                      <a
                        href={`#${emailId}`}
                        className="text-link underline"
                        onClick={(e) => {
                          e.preventDefault();
                          focusField("email");
                        }}
                      >
                        {fieldErrors.email}
                      </a>
                    </li>
                  )}
                  {fieldErrors.password && fieldErrors.password.trim() && (
                    <li className="text-sm">
                      <a
                        href={`#${passwordId}`}
                        className="text-link underline"
                        onClick={(e) => {
                          e.preventDefault();
                          focusField("password");
                        }}
                      >
                        {fieldErrors.password}
                      </a>
                    </li>
                  )}
                </ul>
              </div>
            )}

            <form
              id="signin-form"
              tabIndex={-1}
              noValidate
              onSubmit={handleSubmit}
              className="mt-6 flex flex-col gap-4 outline-none"
            >
              <div className="flex flex-col gap-1">
                <label htmlFor={emailId} className="text-sm font-medium text-ink">
                  Email address
                </label>
                <input
                  ref={emailRef}
                  id={emailId}
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="name@tip.edu.ph"
                  aria-invalid={emailInvalid}
                  aria-describedby={
                    fieldErrors.email && fieldErrors.email.trim() ? emailErrorId : undefined
                  }
                  className={`h-12 rounded-md border bg-panel px-3 text-base text-ink placeholder:text-ink-tertiary ${
                    emailInvalid ? "border-2 border-danger" : "border-border-input hover:border-ink-secondary"
                  }`}
                />
                {emailInvalid && fieldErrors.email && fieldErrors.email.trim() && (
                  <p id={emailErrorId} className="text-sm font-medium text-danger">
                    <span className="sr-only">Error: </span>
                    {fieldErrors.email}
                  </p>
                )}
              </div>

              <div className="flex flex-col gap-1">
                <label htmlFor={passwordId} className="text-sm font-medium text-ink">
                  Password
                </label>
                <div className="relative">
                  <input
                    ref={passwordRef}
                    id={passwordId}
                    type={showPassword ? "text" : "password"}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    aria-invalid={passwordInvalid}
                    aria-describedby={
                      fieldErrors.password && fieldErrors.password.trim() ? passwordErrorId : undefined
                    }
                    className={`h-12 w-full rounded-md border bg-panel px-3 pr-12 text-base text-ink ${
                      passwordInvalid
                        ? "border-2 border-danger"
                        : "border-border-input hover:border-ink-secondary"
                    }`}
                  />
                  <button
                    type="button"
                    aria-pressed={showPassword}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    onClick={() => setShowPassword((v) => !v)}
                    className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-ink-tertiary hover:text-ink"
                  >
                    {showPassword ? (
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        width="20"
                        height="20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
                        <circle cx="12" cy="12" r="3" />
                        <line x1="3" y1="21" x2="21" y2="3" />
                      </svg>
                    ) : (
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        width="20"
                        height="20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
                        <circle cx="12" cy="12" r="3" />
                      </svg>
                    )}
                  </button>
                </div>
                {passwordInvalid && fieldErrors.password && fieldErrors.password.trim() && (
                  <p id={passwordErrorId} className="text-sm font-medium text-danger">
                    <span className="sr-only">Error: </span>
                    {fieldErrors.password}
                  </p>
                )}
              </div>

              <button
                type="submit"
                disabled={login.isPending}
                className="on-dark mt-1 flex h-12 w-full items-center justify-center rounded-md bg-action px-6 text-base font-bold text-on-action hover:bg-action-hover disabled:opacity-60 sm:w-auto sm:min-w-[200px]"
              >
                {login.isPending ? "Signing in" : "Sign in"}
              </button>
              <p role="status" aria-live="polite" className="sr-only">
                {liveMessage}
              </p>
            </form>
          </div>

          <aside className="flex flex-col gap-8 border-t border-border pt-8 lg:border-t-0 lg:pt-0">
            <div>
              <h2 className="text-base font-bold text-ink">Need help signing in?</h2>
              <p className="mt-1 text-sm text-ink-secondary">
                VERIDICAL accounts are created by your program administrator. Self-service account
                recovery is not available yet in this capstone prototype.
              </p>
            </div>
            <div>
              <h2 className="text-base font-bold text-ink">About VERIDICAL</h2>
              <p className="mt-1 text-sm text-ink-secondary">
                VERIDICAL checks capstone manuscripts against your program's own rubric and flags
                anything worth a second look. Every flag shows its evidence. You always make the
                final call.
              </p>
              <p className="mt-2 text-sm">
                <Link to="/" className="text-link underline hover:text-link-hover">
                  Learn more about VERIDICAL
                </Link>
              </p>
            </div>
            <div>
              <p className="text-sm text-ink-secondary">
                Instructor and student accounts each route to their own workspace. Connections are
                secured with TLS; stored data is encrypted.
              </p>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}
