// BUG-551: a definite validation refusal is shown at the field it names and
// is never reported as an unconfirmed save.
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
