import { describe, expect, it } from "vitest";
import type { FlagSummaryOut } from "../api/types";
import { CHECK_KIND_ORDER } from "../domain/checkKind";
import { integrityModuleCounts, selectedIntegrityModule } from "./integrityModuleState";

describe("integrity module presentation", () => {
  it("retains all four empty checks and preserves an unexpected recorded kind", () => {
    const empty = integrityModuleCounts([]);
    expect(empty.map(module => module.kind)).toEqual(CHECK_KIND_ORDER);
    expect(empty.every(module => module.findings === 0 && module.open === 0)).toBe(true);
    const counts = integrityModuleCounts([
      { kind: "internal_agreement", open: true }, { kind: "internal_agreement", open: false },
      { kind: "future_check", open: true },
    ]);
    expect(counts[0]).toMatchObject({ findings: 2, open: 1 });
    expect(counts.at(-1)).toMatchObject({ kind: "future_check", label: "Future check", findings: 1, open: 1 });
  });

  it("validates module queries while keeping empty modules selectable", () => {
    const flags = [{ check_kind: "citation_integrity", overridden: false, severity: "low" }, { check_kind: "future_check", overridden: false, severity: "high" }] as FlagSummaryOut[];
    expect(selectedIntegrityModule("statistical_forensics", flags)).toBe("statistical_forensics");
    expect(selectedIntegrityModule("future_check", flags)).toBe("future_check");
    expect(selectedIntegrityModule("not_saved", flags, "high")).toBe("future_check");
    expect(selectedIntegrityModule(null, [])).toBe("internal_agreement");
    expect(flags.map(flag => flag.check_kind)).toEqual(["citation_integrity", "future_check"]);
  });
});
