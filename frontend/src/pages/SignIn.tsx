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
          const from = (location.state as LocationState | null)?.from?.pathname ?? "/dashboard";
          navigate(from, { replace: true });
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
    // Genuine split-screen (V-056), not a header + two-column-in-a-sea-of-
    // white: the old layout only occupied the top ~35% of the viewport,
    // which reads as unfinished next to any real institutional sign-in
    // (GOV.UK/USWDS both fill the viewport). The black-chrome panel is
    // also where TIP identity is most load-bearing — first contact.
    <div className="flex min-h-screen flex-col lg:flex-row">
      <a
        href="#signin-form"
        className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2 focus-visible:z-(--z-skip-link) focus-visible:rounded-md focus-visible:bg-panel focus-visible:px-4 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-ink"
      >
        Skip to sign-in form
      </a>

      <div className="flex flex-none flex-col border-b-[3px] border-accent bg-tip-chrome px-4 py-5 sm:px-6 lg:w-[42%] lg:border-r-[3px] lg:border-b-0 lg:px-10 lg:py-12">
        <Link to="/" className="on-dark flex items-center gap-2 rounded-sm">
          <span
            aria-hidden="true"
            className="flex h-7 w-7 items-center justify-center rounded-sm bg-accent text-sm font-bold text-on-tip-yellow sm:h-8 sm:w-8"
          >
            V
          </span>
          <span className="text-base font-bold tracking-header text-on-tip-chrome sm:text-md">
            VERIDICAL
          </span>
        </Link>
        {/*
         * V-056 follow-up (ux-critic finding): was `justify-between` on
         * the parent, pinning this block to the panel's bottom edge and
         * leaving ~500px of unbroken dark space above it at 1440px — the
         * one place this screen didn't clear the "professional next to
         * GOV.UK/USWDS/Material 3" divergence-gate clause. Vertically
         * centered in the REMAINING space below the logo instead, which
         * is what those systems' own split-screen patterns do.
         */}
        <div className="hidden flex-1 flex-col justify-center lg:flex">
          <p className="max-w-[360px] text-xl leading-snug font-bold tracking-display text-on-tip-chrome">
            Quality and integrity assurance for BSIT capstone manuscripts.
          </p>
          <p className="mt-4 max-w-[360px] text-sm text-neutral-300">
            Every flag shows its evidence. You always make the final call.
          </p>
          <Link
            to="/"
            className="on-dark mt-4 inline-block text-sm font-medium text-on-tip-chrome underline hover:text-neutral-300"
          >
            Learn more about VERIDICAL
          </Link>
        </div>
      </div>

      <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-6 lg:px-10">
        <div className="w-full max-w-[440px]">
          <div>
            <h1
              ref={headingRef}
              tabIndex={-1}
              className="text-lg font-bold text-ink sm:text-2xl"
            >
              Sign in to VERIDICAL
            </h1>
            <p className="mt-2 text-base text-ink-secondary lg:hidden">
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
              className="mt-6 flex flex-col gap-4"
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

          {/*
           * Collapsed to a compact footnote (V-056) — was a second full
           * column of "Need help" + "About VERIDICAL" + a TLS note, which
           * left the form floating in a sea of empty viewport (the
           * diagnosed problem). "About VERIDICAL" now lives in the left
           * brand panel instead of being duplicated here.
           */}
          <aside className="mt-8 border-t border-border pt-4">
            <h2 className="sr-only">Need help signing in?</h2>
            <p className="text-xs text-ink-tertiary">
              VERIDICAL accounts are created by your program administrator; self-service
              recovery isn't available yet in this capstone prototype. Instructor and student
              accounts route to their own workspace; connections are secured with TLS.
            </p>
          </aside>
        </div>
      </main>
    </div>
  );
}
