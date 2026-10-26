/// <reference types="node" />
import { describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { criterionSource, evidenceFor, evidenceRegion, flagSourcePagePair, publicEvidenceImages, sourceEvidence } from "./sourceEvidence";
import { criterionAnchorRegion } from "../document/criterionAnchor";

describe("complete public source bindings", () => {
  it("pairs the saved claim and reference context without inventing an external citation", () => {
    const entries = evidenceFor("flag", 4);
    const context = { run: 1, manuscript: 1, sourceHash: entries[0].source_sha256, flagId: 4 };
    expect(flagSourcePagePair(entries, context).map(entry => [entry.role, entry.page, entry.label])).toEqual([
      ["single", 7, "Unidentified supporting-study claim"],
      ["context", 109, "Reference section as published"],
    ]);
    for (const change of [{ run: 2 }, { manuscript: 202 }, { sourceHash: "changed" }, { flagId: 1 }]) expect(flagSourcePagePair(entries, { ...context, ...change })).toEqual([]);
    for (const change of [{ page: 1 }, { image_id: "invented" }, { image_sha256: "changed" }, { quote: "prefix" }, { role: "statement_b" }]) expect(flagSourcePagePair([{ ...entries[0], ...change }, entries[1]], context)).toEqual([]);
    expect(flagSourcePagePair(entries.slice(0, 1), context)).toEqual([]);
    expect(flagSourcePagePair([...entries, entries[0]], context)).toEqual([]);
    expect(flagSourcePagePair([entries[0], entries[0]], context)).toEqual([]);
  });

  it("retains the original statement pair and separate saved boxes on one page", () => {
    for (const flagId of [1, 3]) {
      const entries = evidenceFor("flag", flagId);
      expect(flagSourcePagePair(entries, { run: 1, manuscript: 1, sourceHash: entries[0].source_sha256, flagId })).toEqual(entries);
    }
  });
  it("retains each criterion/statement, exact geometry qualifications and content-addressed images", () => {
    expect(sourceEvidence).toHaveLength(18);
    expect(sourceEvidence.filter(entry => entry.precision === "page_bbox")).toHaveLength(17);
    const pages = JSON.parse(readFileSync(resolve("public/sample-assets/manuscript-text.json"), "utf8")) as { text: string }[];
    for (const entry of sourceEvidence) {
      expect(pages[entry.page! - 1].text.replace(/\s+/g, " ")).toContain(entry.quote.replace(/\s+/g, " "));
      expect(createHash("sha256").update(entry.quote).digest("hex")).toBe(entry.quote_sha256);
      const png = readFileSync(resolve(`public/sample-assets/evidence/${entry.image_id}.png`));
      expect(createHash("sha256").update(png).digest("hex")).toBe(entry.image_sha256);
      expect(entry.image_id).toBe(`e-${entry.image_sha256!.slice(0, 24)}`);
    }
    expect(evidenceFor("flag", 1).map(entry => entry.page)).toEqual([29, 109]);
    expect(evidenceFor("criterion", 1).map(entry => entry.page)).toEqual([109, 5, 29, 37, 103]);
    expect(evidenceFor("criterion", 2).map(entry => entry.page)).toEqual([23, 32, 33]);
    expect(publicEvidenceImages()).toHaveLength(18);
  });

  it("rejects mismatched source, run, subject, role, quote, anchor and synthetic location identity", () => {
    const entry = evidenceFor("flag", 1)[0];
    const context = { run: 1, manuscript: 1, sourceHash: entry.source_sha256, kind: "flag" as const, subjectId: 1, role: entry.role, index: entry.evidence_index, quote: entry.quote, anchor: entry.anchor };
    expect(evidenceRegion(structuredClone(entry), context, 1)?.kind).toBe("page_bbox");
    for (const change of [{ run: 2 }, { manuscript: 202 }, { sourceHash: "changed" }, { subjectId: 2 }, { role: "statement_b" }, { index: 9 }, { quote: "prefix" }, { anchor: "page 1" }]) expect(evidenceRegion(entry, { ...context, ...change }, 1)).toBeNull();
    for (const change of [{ quote: "altered", quote_sha256: entry.quote_sha256 }, { anchor: "page 1" }, { role: "statement_b" }, { subject: { kind: "flag" as const, id: 2 } }]) expect(evidenceRegion({ ...entry, ...change }, context, 1)).toBeNull();
    expect(evidenceRegion(entry, context, 202)).toBeNull();
    expect(criterionSource(1, { quote: entry.quote, anchor: "page 1" })).toBeUndefined();
  });

  it("keeps paragraph zero valid and refuses to turn prose/ranges into evidence pages", () => {
    expect(criterionAnchorRegion("paragraph 0")?.paragraph).toBe(0);
    for (const anchor of ["pp37-50", "page 37-50", "context on page 23", "page 0", "unplaced excerpt", "page 999999999999999999999999", "paragraph 99999999999999999999999"]) expect(criterionAnchorRegion(anchor)).toBeNull();
  });
});
