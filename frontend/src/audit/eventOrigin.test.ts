import { describe, expect, it } from "vitest";
import { auditEventOrigin } from "./eventOrigin";
import { recordedActor } from "./instructorEventDetails";

// Events whose origin the stored entry cannot prove, so the Audit keeps
// saying "Origin unavailable" for them on purpose.
const ORIGIN_NOT_PROVABLE = new Map([
  ["library_entry_removed", "an instructor or a maintenance cleanup can remove a Library record"],
  ["library_orphan_corpus_rows_removed", "an operator cleanup, neither an instructor nor the pipeline"],
]);
// Event names the backend builds from a check kind. Only the integrity checks
// produce them.
const INTEGRITY_KINDS = ["internal_agreement", "citation_integrity", "statistical_forensics", "originality_reuse"];
const TEMPLATES = new Map([
  ["{kind.value}_check_computed", INTEGRITY_KINDS.map(kind => `${kind}_check_computed`)],
  ["{result.kind.value}_source_scope_recorded", INTEGRITY_KINDS.map(kind => `${kind}_source_scope_recorded`)],
]);

async function backendEventTypes(): Promise<string[]> {
  // @ts-ignore Node built-in module in test runner
  const fs = await import("node:fs");
  // @ts-ignore Node built-in module in test runner
  const path = await import("node:path");
  const proc = (globalThis as unknown as { process?: { cwd: () => string } }).process;
  const appDir = path.resolve(proc ? proc.cwd() : ".", "../backend/app");
  const found = new Set<string>();
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".py")) {
        for (const match of fs.readFileSync(full, "utf8").matchAll(/event_type=(f?)"([^"]+)"/g)) {
          if (!match[1]) found.add(match[2]);
          else {
            const expanded = TEMPLATES.get(match[2]);
            if (!expanded) throw new Error(`Unlisted event name template in ${full}: ${match[2]}`);
            expanded.forEach(name => found.add(name));
          }
        }
      }
    }
  };
  walk(appDir);
  return [...found].sort();
}

describe("audit event origin", () => {
  it("BUG-559: treats a confirmed finding as the instructor's own action", () => {
    const entry = { event_type: "flag_finding_confirmed", manuscript_id: null, check_run_id: 32, created_at: "2026-10-30T10:00:00Z" };
    expect(auditEventOrigin("flag_finding_confirmed")).toBe("instructor");
    expect(recordedActor({ flag_id: 558, instructor_id: 1, note: null }, 1, entry)).toBe("You");
    expect(recordedActor({ flag_id: 558, instructor_id: 2, note: null }, 1, entry)).toBe("Instructor #2");
  });

  it("classifies every event the backend writes, or names why its origin is not provable", async () => {
    const events = await backendEventTypes();
    expect(events).toContain("flag_finding_confirmed");
    expect(events.length).toBeGreaterThan(30);
    const unclassified = events.filter(event => auditEventOrigin(event) === "unknown" && !ORIGIN_NOT_PROVABLE.has(event));
    expect(unclassified).toEqual([]);
    // A listed exception that the backend stopped writing, or that is now
    // classified, would hide a later regression.
    for (const event of ORIGIN_NOT_PROVABLE.keys()) {
      expect(events).toContain(event);
      expect(auditEventOrigin(event)).toBe("unknown");
    }
  });
});
