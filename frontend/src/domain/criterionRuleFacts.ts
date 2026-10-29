// F3.2/F8, screens4h/4l/4k: one saved-fact vocabulary for screen and print.
import COPY from "../../../backend/app/report/data/rule_facts_copy.json";

export interface RuleFactsPresentation { paragraphs: string[]; headings: string[] }
export const RULE_FACTS_HEADING = COPY.heading;
export const EARLIER_RULE_FACTS_HEADING = COPY.earlier_heading;

const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v)
  && (Object.getPrototypeOf(v) === Object.prototype || Object.getPrototypeOf(v) === null);
const text = (v: unknown): v is string => typeof v === "string" && Boolean(v.trim());
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const count = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const fill = (template: string, values: Record<string, unknown>) => template.replace(/\{(\w+)\}/g, (_match, key) => String(values[key]));

export function criterionRuleFacts(raw: unknown): RuleFactsPresentation | undefined {
  if (!record(raw) || !Object.hasOwn(raw, "rule_id")) return;
  const v = raw;
  switch (v.rule_id) {
    case "required_section_present":
      if (!text(v.target) || !(v.matched_title == null || text(v.matched_title))) return;
      return { paragraphs: [v.matched_title == null ? fill(COPY.heading_search, v) : fill(COPY.heading_match, v), COPY.heading_scope], headings: [] };
    case "section_order":
      if (!Array.isArray(v.checked_sections) || v.checked_sections.length < 2 || !v.checked_sections.every(text)) return;
      return { paragraphs: [COPY.order, COPY.order_scope], headings: v.checked_sections };
    case "page_limit":
    case "reference_count_min_max": {
      const b = record(v.expected) ? v.expected : undefined;
      if (!count(v.actual_count) || (v.rule_id === "page_limit" && v.actual_count === 0) || !b
        || !finite(b.low) || b.low < 0 || typeof b.kind !== "string" || !["min", "max", "exact", "range"].includes(b.kind)) return;
      if (b.kind === "range" ? !finite(b.high) || b.high < b.low : b.high != null) return;
      const bound = fill(COPY[`bound_${b.kind}` as "bound_min" | "bound_max" | "bound_exact" | "bound_range"], b);
      const paragraphs = v.rule_id === "page_limit"
        ? [fill(COPY.pages, { count: v.actual_count }), fill(COPY.pages_bound, { bound })]
        : [fill(COPY.references, { count: v.actual_count }), fill(COPY.references_bound, { bound }), COPY.references_scope];
      return { paragraphs, headings: [] };
    }
    case "section_word_count": {
      const b = record(v.expected) ? v.expected : undefined;
      if (!count(v.actual_words) || v.actual_words === 0 || !count(v.excluded_lines) || !text(v.section_title) || !text(v.counted_until)
        || !b || !finite(b.low) || b.low < 0 || typeof b.kind !== "string" || !["min", "max", "exact", "range"].includes(b.kind)) return;
      if (b.kind === "range" ? !finite(b.high) || b.high < b.low : b.high != null) return;
      const bound = fill(COPY[`bound_${b.kind}` as "bound_min" | "bound_max" | "bound_exact" | "bound_range"], b);
      return {
        paragraphs: [fill(COPY.words, { count: v.actual_words, section_title: v.section_title, counted_until: v.counted_until }),
          fill(COPY.words_bound, { bound }), fill(COPY.words_scope, { excluded_lines: v.excluded_lines })],
        headings: [],
      };
    }
    case "table_formatting_presence":
      if (!count(v.total_tables) || v.total_tables === 0 || !count(v.captioned) || v.captioned > v.total_tables) return;
      return { paragraphs: [fill(COPY.captions, v), COPY.captions_scope], headings: [] };
    case "margins_spacing":
      if (v.checked_pages_or_sections != null) {
        if (!count(v.checked_pages_or_sections) || v.checked_pages_or_sections === 0 || v.side != null || v.actual_pts != null) return;
        return { paragraphs: [fill(COPY.margins_count, { count: v.checked_pages_or_sections }), COPY.margins_scope], headings: [] };
      }
      if (typeof v.side !== "string" || !["left", "top", "right", "bottom"].includes(v.side) || !finite(v.actual_pts)) return;
      return { paragraphs: [fill(COPY.margins_measurement, v)], headings: [] };
    case "citation_style_sniff":
      if (v.threshold_relation !== "met" && v.threshold_relation !== "below") return;
      return { paragraphs: [COPY.citation_pattern, v.threshold_relation === "met" ? COPY.citation_met : COPY.citation_below, COPY.citation_scope], headings: [] };
  }
}
