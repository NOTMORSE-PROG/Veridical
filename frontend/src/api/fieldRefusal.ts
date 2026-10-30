// BUG-551: a definite validation refusal is shown at the field it names and
// is never reported as an unconfirmed save.
import { useEffect, type RefObject } from "react";
import { ApiError, VALIDATION_FALLBACK } from "./client";

export function isValidationRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === "validation_error";
}

// The server's sentence for one field of a refused request, if it named it.
export function fieldRefusal(error: unknown, field: string): string | null {
  return isValidationRefusal(error) ? error.fieldMessages.get(field) ?? null : null;
}

// Said for a request refused as a whole: the instructor's own text was fine,
// so the generic "check what you entered" would send them to rewrite it.
export const REQUEST_REFUSED_FALLBACK = "This request was not accepted. Copy anything you typed, then reload the page and try again.";

// The sentences for whatever was refused that no field on this form shows,
// even when a shown field was refused in the same response.
export function formRefusal(error: unknown, fields: readonly string[], fallback = REQUEST_REFUSED_FALLBACK): string | null {
  if (!isValidationRefusal(error)) return null;
  const others = [...error.fieldMessages].filter(([field]) => !fields.includes(field)).map(([, message]) => message);
  const messages = others.length > 0 ? others : fields.some((field) => error.fieldMessages.has(field)) ? [] : [error.message];
  const said = [...new Set(messages.map((message) => message === VALIDATION_FALLBACK ? fallback : message))];
  return said.length > 0 ? said.join(" ") : null;
}

export const REFUSAL_NOTHING_CHANGED = "Nothing was changed.";
export const REFUSAL_TITLE = "Nothing was changed";

// After a refusal the submit button was busy, so focus fell to the page; put
// it back on the refused field and bring its message into view.
export function useRefusalFocus(refusal: string | null, field: RefObject<HTMLTextAreaElement | HTMLInputElement | null>) {
  useEffect(() => {
    if (!refusal || !field.current) return;
    field.current.focus();
    const message = field.current.getAttribute("aria-describedby");
    if (message) document.getElementById(message.split(" ").pop() ?? "")?.scrollIntoView({ block: "nearest" });
  }, [refusal, field]);
}

// BUG-543: a list page past the server's cap is refused; asking again gives
// the same answer, so the screen says the page does not exist.
export function isPagePastEnd(error: unknown): boolean {
  return isValidationRefusal(error) && error.fieldMessages.has("page");
}

// BUG-555: the server's own busy refusals, which it sends before acting
// (backend/app/errors.py). A bare 503 from a gateway proves nothing about
// whether the write happened, so only these codes say nothing changed.
const SERVICE_BUSY_CODES: ReadonlySet<string> = new Set([
  "rate_limited", "quota_exhausted", "document_preparation_busy", "ai_not_configured", "source_read_unavailable",
]);

export function isServiceBusy(error: unknown): error is ApiError {
  return error instanceof ApiError && (error.status === 429 || error.status === 503) && SERVICE_BUSY_CODES.has(error.code);
}

// Said when a save failed in a way that leaves its outcome unknown.
export const OUTCOME_UNKNOWN_COPY = "It is not known whether this was saved. Reload the page to see the current state before trying again.";

export const SERVICE_BUSY_COPY = {
  title: "The service is busy",
  body: "Nothing was changed and what you typed is kept. Try again in a moment.",
} as const;
