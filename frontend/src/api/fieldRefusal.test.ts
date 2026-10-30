import { describe, expect, it } from "vitest";
import { ApiError, VALIDATION_FALLBACK } from "./client";
import { fieldRefusal, formRefusal, isValidationRefusal, REQUEST_REFUSED_FALLBACK } from "./fieldRefusal";

const refused = new ApiError(422, "validation_error", "Reason refused.", ["reason"], new Map([["reason", "Reason refused."]]));
const unplaced = new ApiError(422, "validation_error", "Something else was refused.", ["expected_revision"], new Map([["expected_revision", "Something else was refused."]]));

describe("a definite refusal at its field", () => {
  it("reads the sentence for the field it names", () => {
    expect(fieldRefusal(refused, "reason")).toBe("Reason refused.");
    expect(fieldRefusal(refused, "note")).toBeNull();
  });

  it("says a refusal of another field once for the form, not at a field", () => {
    expect(formRefusal(unplaced, ["reason"])).toBe("Something else was refused.");
    expect(formRefusal(refused, ["reason"])).toBeNull();
  });

  it("still says a refusal of another field when a shown field was refused too", () => {
    const both = new ApiError(422, "validation_error", "Reason refused.", ["reason", "flag_ids"], new Map([["reason", "Reason refused."], ["flag_ids", "Too many."]]));
    expect(formRefusal(both, ["reason"])).toBe("Too many.");
  });

  it("does not tell the instructor to check their text when the request was refused", () => {
    const generic = new ApiError(422, "validation_error", VALIDATION_FALLBACK, ["expected_revision"], new Map([["expected_revision", VALIDATION_FALLBACK]]));
    expect(formRefusal(generic, ["reason"])).toBe(REQUEST_REFUSED_FALLBACK);
    expect(formRefusal(generic, ["reason"], "Close and reselect.")).toBe("Close and reselect.");
  });

  it("never treats an unconfirmed save as a refusal", () => {
    expect(isValidationRefusal(new ApiError(502, "invalid_response", "Could not confirm."))).toBe(false);
    expect(fieldRefusal(new ApiError(409, "conflict", "Changed."), "reason")).toBeNull();
  });
});
