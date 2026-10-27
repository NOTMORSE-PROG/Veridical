import { describe, expect, it } from "vitest";
import { formatFileSize } from "./fileSize";

describe("formatFileSize (BUG-319)", () => {
  it("formats 0 bytes as 0 KB and never 0.0 MB", () => {
    expect(formatFileSize(0)).toBe("0 KB");
  });

  it("formats small non-zero files in KB rather than 0.0 MB", () => {
    expect(formatFileSize(512)).toBe("1 KB");
    expect(formatFileSize(2 * 1024)).toBe("2 KB");
    expect(formatFileSize(999 * 1024)).toBe("999 KB");
  });

  it("formats sizes at or above 1 MB with one decimal place", () => {
    expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatFileSize(1.5 * 1024 * 1024)).toBe("1.5 MB");
  });
});
