// F2/F3, screens4e/4m: content cues use saved facts; IDs remain secondary.
import type { RubricListItem } from "../api/types";

export function formatCriteriaCount(count: number): string {
  return Number.isSafeInteger(count) && count >= 0 ? `${count} ${count === 1 ? "criterion" : "criteria"}` : "Criterion count unavailable";
}

export function formatChoiceContext(value: RubricListItem, siblings: readonly RubricListItem[]): string {
  const created = new Date(value.created_at);
  const date = typeof value.created_at === "string" && value.created_at.trim() && Number.isFinite(created.getTime())
    ? `Added ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(created)}` : "Date unavailable";
  const duplicate = siblings.some(other => other.id !== value.id && other.title === value.title && other.version === value.version);
  return [formatCriteriaCount(value.criteria_count), value.program, date, duplicate ? `Format #${value.id}` : null].filter(Boolean).join(" · ");
}

export function formatChoiceLabel(value: RubricListItem, siblings: readonly RubricListItem[]): string {
  return `${value.title} (v${value.version}) · ${formatChoiceContext(value, siblings)}`;
}
