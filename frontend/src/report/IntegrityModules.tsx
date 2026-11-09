// F4–F7/4h: selection is independent of saved execution state and finding counts.
import type { IntegrityModuleOut } from "../api/types";
import { checkKindMeta } from "../domain/checkKind";
import { Button } from "../ui/Button";

export const INTEGRITY_MODULES = [
  { kind: "internal_agreement", label: "Internal agreement" },
  { kind: "citation_integrity", label: "Citation integrity" },
  { kind: "statistical_forensics", label: "Statistical checks" },
  { kind: "originality_reuse", label: "Originality and reuse" },
] as const;
export type IntegrityKind = typeof INTEGRITY_MODULES[number]["kind"];
export function selectedIntegrityModule(value: string | null): IntegrityKind {
  return INTEGRITY_MODULES.find(module => module.kind === value)?.kind ?? INTEGRITY_MODULES[0].kind;
}
const STATE_LABEL: Record<IntegrityModuleOut["state"], string> = {
  completed: "Completed", partial: "Ran only in part", incomplete: "Not fully run",
  not_applicable: "Not applicable", not_recorded: "Status not recorded",
};

export function IntegrityModuleNavigation({ selected, onSelect, modules, counts, controls, stateLabels, countLabels, hint = "Choose a check to review its findings and source evidence." }: {
  selected: IntegrityKind; onSelect: (kind: IntegrityKind) => void;
  modules?: IntegrityModuleOut[]; counts: ReadonlyMap<string, number>; controls: string;
  // A more exact label for a check's state, e.g. originality's "Some text not compared".
  stateLabels?: Partial<Record<IntegrityKind, string>>;
  /** The count line in the page's own units, e.g. findings and comparison leads. */
  countLabels?: Partial<Record<IntegrityKind, string>>;
  /** Left out where the page already says how to use the checks. */
  hint?: string | null;
}) {
  const savedRecords = (n: number) => `${n} saved ${n === 1 ? "record" : "records"}`;
  return <nav className="signal-integrity-modules" aria-label="Manuscript check modules">{hint && <p className="signal-field-hint">{hint}</p>}<div role="group" aria-label="Integrity check modules">
    {INTEGRITY_MODULES.map(module => <Button key={module.kind} variant="secondary"
      aria-pressed={selected === module.kind} aria-controls={controls} onClick={() => onSelect(module.kind)}>
      <span>{module.label}</span><small>{stateLabels?.[module.kind] ?? STATE_LABEL[modules?.find(saved => saved.check_kind === module.kind)?.state ?? "not_recorded"]}</small>
      <small>{countLabels?.[module.kind] ?? savedRecords(counts.get(module.kind) ?? 0)}</small>
    </Button>)}
  </div></nav>;
}

export function IntegrityModuleSummary({ kind }: { kind: IntegrityKind }) {
  return <header className="signal-integrity-module-summary"><p className="signal-section-kicker">Selected check</p><h3>{INTEGRITY_MODULES.find(module => module.kind === kind)?.label}</h3><p>{checkKindMeta(kind).verificationPrompt}</p></header>;
}
