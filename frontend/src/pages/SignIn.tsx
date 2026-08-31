import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { ApiError } from "../api/client";
import { useLogin, useMe } from "../auth/useAuth";
import { useRouteFocus } from "../routing/useRouteFocus";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { SignalMark, SignalWordmark } from "../ui/SignalMark";

interface IntendedLocation {
  pathname: string;
  search?: string;
  hash?: string;
}

interface LocationState {
  from?: IntendedLocation;
}

interface FieldErrors {
  email?: string;
  password?: string;
}

function intendedDestination(state: unknown): string {
  const from = (state as LocationState | null)?.from;
  if (!from?.pathname.startsWith("/") || from.pathname.startsWith("//")) return "/dashboard";
  return `${from.pathname}${from.search ?? ""}${from.hash ?? ""}`;
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
  const [errorAttempt, setErrorAttempt] = useState(0);

  const emailId = useId();
  const passwordId = useId();
  const emailErrorId = `${emailId}-error`;
  const passwordErrorId = `${passwordId}-error`;
  const summaryRef = useRef<HTMLDivElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const destination = intendedDestination(location.state);
  const hasIntendedDestination = destination !== "/dashboard";

  useRouteFocus("Sign in - VERIDICAL", headingRef);

  const hasErrors = Boolean(formError || fieldErrors.email || fieldErrors.password);

  useEffect(() => {
    if (errorAttempt > 0) summaryRef.current?.focus();
  }, [errorAttempt]);

  if (!mePending && me) return <Navigate to={destination} replace />;

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
      setErrorAttempt((attempt) => attempt + 1);
      return;
    }

    setLiveMessage("Signing in. Please wait.");
    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: () => {
          setLiveMessage("");
          navigate(destination, { replace: true });
        },
        onError: (error) => {
          setLiveMessage("");
          if (error instanceof ApiError && error.status === 401) {
            setFormError(
              "We could not sign you in with those details. Check your email and password and try again.",
            );
            setFieldErrors({ email: " ", password: " " });
          } else {
            setFormError(
              "VERIDICAL could not sign you in. Your details were not accepted. Try again in a moment.",
            );
          }
          setErrorAttempt((attempt) => attempt + 1);
        },
      },
    );
  }

  const emailInvalid = Boolean(fieldErrors.email);
  const passwordInvalid = Boolean(fieldErrors.password);

  return (
    <div className="signal-theme signal-auth-frame" data-design="signal">
      <a href="#signin-form" className="signal-skip-link">
        Skip to sign-in form
      </a>

      <header className="signal-public-header">
        <Link to="/" className="signal-brand-link signal-on-dark" aria-label="VERIDICAL home">
          <SignalMark inverse />
          <SignalWordmark inverse />
        </Link>
        <p className="signal-project-label">
          Student capstone at T.I.P. Manila. Not an official service.
        </p>
      </header>

      <main className="signal-auth-main">
        <section className="signal-auth-statement" aria-labelledby="auth-statement">
          <p className="signal-eyebrow">Instructor workspace</p>
          <p id="auth-statement" className="signal-auth-statement__lead">
            Review the evidence. Make the final decision.
          </p>
          <p>
            Prepare criteria, check manuscripts, verify the evidence, and record the instructor
            outcome in one traceable workspace.
          </p>
        </section>

        <section className="signal-auth-card" aria-labelledby="signin-heading">
          <p className="signal-eyebrow">Instructor access</p>
          <h1 id="signin-heading" ref={headingRef} tabIndex={-1}>
            Sign in to VERIDICAL
          </h1>
          <p className="signal-auth-intro">
            Use the instructor account issued by your program administrator.
          </p>
          {hasIntendedDestination && (
            <p className="signal-auth-intro">
              After sign in, you will return to the page you requested.
            </p>
          )}

          {hasErrors && (
            <Alert
              ref={summaryRef}
              title="There is a problem"
              tone="error"
              role="alert"
              tabIndex={-1}
              className="signal-error-summary"
            >
              <ul>
                {formError && <li>{formError}</li>}
                {fieldErrors.email?.trim() && (
                  <li>
                    <a
                      href={`#${emailId}`}
                      onClick={(event) => {
                        event.preventDefault();
                        focusField("email");
                      }}
                    >
                      {fieldErrors.email}
                    </a>
                  </li>
                )}
                {fieldErrors.password?.trim() && (
                  <li>
                    <a
                      href={`#${passwordId}`}
                      onClick={(event) => {
                        event.preventDefault();
                        focusField("password");
                      }}
                    >
                      {fieldErrors.password}
                    </a>
                  </li>
                )}
              </ul>
            </Alert>
          )}

          <form id="signin-form" tabIndex={-1} noValidate onSubmit={handleSubmit} className="signal-form">
            <div className="signal-field">
              <label htmlFor={emailId}>Email address</label>
              <input
                ref={emailRef}
                id={emailId}
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@tip.edu.ph"
                aria-invalid={emailInvalid}
                aria-describedby={fieldErrors.email?.trim() ? emailErrorId : undefined}
              />
              {fieldErrors.email?.trim() && (
                <p id={emailErrorId} className="signal-field__error">
                  <span className="sr-only">Error: </span>
                  {fieldErrors.email}
                </p>
              )}
            </div>

            <div className="signal-field">
              <label htmlFor={passwordId}>Password</label>
              <div className="signal-password">
                <input
                  ref={passwordRef}
                  id={passwordId}
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={passwordInvalid}
                  aria-describedby={fieldErrors.password?.trim() ? passwordErrorId : undefined}
                />
                <button
                  type="button"
                  aria-pressed={showPassword}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  onClick={() => setShowPassword((visible) => !visible)}
                  className="signal-password__toggle"
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
              {fieldErrors.password?.trim() && (
                <p id={passwordErrorId} className="signal-field__error">
                  <span className="sr-only">Error: </span>
                  {fieldErrors.password}
                </p>
              )}
            </div>

            <div className="signal-form__actions">
              <Button type="submit" variant="brand" busy={login.isPending}>
                {login.isPending ? "Signing in" : "Sign in"}
              </Button>
            </div>
            <p role="status" aria-live="polite" className="sr-only">
              {liveMessage}
            </p>
          </form>

          <details className="signal-help">
            <summary>Help with instructor access</summary>
            <p>
              Accounts are issued by your program administrator. Self-service password recovery
              is not available in this capstone prototype. Ask the administrator who issued your
              account if you cannot sign in.
            </p>
          </details>
        </section>
      </main>
    </div>
  );
}
