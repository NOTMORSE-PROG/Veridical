import type { FlagSummaryOut } from "../api/types";
import { CHECK_KIND_ORDER, CHECK_KIND_SHORT_LABEL, humanize } from "../domain/checkKind";

export const CHECK_MODULE_PARAM = "check_module";

export interface IntegrityModuleCount {
  kind: string;
  label: string;
  findings: number;
  open: number;
}

export function integrityModuleLabel(kind: string): string {
  return Object.hasOwn(CHECK_KIND_SHORT_LABEL, kind) ? CHECK_KIND_SHORT_LABEL[kind] : humanize(kind);
}

// Callers supply their existing finding projection; location records and
// clustered findings must not acquire the same count by accident.
export function integrityModuleCounts(records: readonly { kind: string; open: boolean }[]): IntegrityModuleCount[] {
  const kinds: string[] = [...CHECK_KIND_ORDER];
  for (const record of records) if (!kinds.includes(record.kind)) kinds.push(record.kind);
  return kinds.map(kind => ({
    kind, label: integrityModuleLabel(kind),
    findings: records.filter(record => record.kind === kind).length,
    open: records.filter(record => record.kind === kind && record.open).length,
  }));
}

export function selectedIntegrityModule(requested: string | null, flags: readonly FlagSummaryOut[], severity?: string): string {
  const kinds: readonly string[] = CHECK_KIND_ORDER;
  if (requested && (kinds.includes(requested) || flags.some(flag => flag.check_kind === requested))) return requested;
  const open = flags.filter(flag => !flag.overridden);
  const preferred = severity ? open.find(flag => flag.severity === severity) : undefined;
  return preferred?.check_kind ?? open[0]?.check_kind ?? flags[0]?.check_kind ?? CHECK_KIND_ORDER[0];
}
