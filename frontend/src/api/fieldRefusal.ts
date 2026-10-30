// BUG-551: a definite validation refusal is shown at the field it names and
// is never reported as an unconfirmed save.
import { useEffect, type RefObject } from "react";
import { ApiError } from "./client";

export function isValidationRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.code === "validation_error";
}

// The server's sentence for one field of a refused request, if it named it.
export function fieldRefusal(error: unknown, field: string): string | null {
  return isValidationRefusal(error) ? error.fieldMessages.get(field) ?? null : null;
}

// The sentence for a refusal that named no field this form shows.
export function formRefusal(error: unknown, fields: readonly string[]): string | null {
  if (!isValidationRefusal(error)) return null;
  return fields.some((field) => error.fieldMessages.has(field)) ? null : error.message;
}

export const REFUSAL_NOTHING_CHANGED = "Nothing was changed.";

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
