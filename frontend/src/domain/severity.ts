export type Severity = "high" | "med" | "low";

const SEVERITY_LABEL: Record<Severity, string> = {
  high: "High severity",
  med: "Medium severity",
  low: "Low severity",
};

export function severityLabel(severity: Severity): string {
  return SEVERITY_LABEL[severity];
}
