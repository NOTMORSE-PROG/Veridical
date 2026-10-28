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
  completed: "Processing completed", partial: "Partly assessed", incomplete: "Not fully run",
  not_applicable: "Not applicable", not_recorded: "Execution not recorded",
};

export function IntegrityModuleNavigation({ selected, onSelect, modules, counts, controls }: {
  selected: IntegrityKind; onSelect: (kind: IntegrityKind) => void;
  modules?: IntegrityModuleOut[]; counts: ReadonlyMap<string, number>; controls: string;
}) {
  return <nav className="signal-integrity-modules" aria-label="Manuscript check modules"><p className="signal-field-hint">Choose a check to review its findings and source evidence.</p><div role="group" aria-label="Integrity check modules">
    {INTEGRITY_MODULES.map(module => <Button key={module.kind} variant="secondary"
      aria-pressed={selected === module.kind} aria-controls={controls} onClick={() => onSelect(module.kind)}>
      <span>{module.label}</span><small>{STATE_LABEL[modules?.find(saved => saved.check_kind === module.kind)?.state ?? "not_recorded"]}</small>
      <small>{counts.get(module.kind) ?? 0} saved records</small>
    </Button>)}
  </div></nav>;
}

export function IntegrityModuleSummary({ kind }: { kind: IntegrityKind }) {
  return <header className="signal-integrity-module-summary"><p className="signal-section-kicker">Selected check</p><h3>{INTEGRITY_MODULES.find(module => module.kind === kind)?.label}</h3><p>{checkKindMeta(kind).verificationPrompt}</p></header>;
}
