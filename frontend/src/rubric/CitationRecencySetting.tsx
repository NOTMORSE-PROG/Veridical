// V-092 (screen 4m): the format's citation recency note, set for every
// version of the format. Each value says whether this format set it or the
// default applies.
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { fieldRefusal, formRefusal, isValidationRefusal, OUTCOME_UNKNOWN_COPY, useRefusalFocus } from "../api/fieldRefusal";
import type { CitationRecencySettingsIn, CitationRecencySettingsOut, RecencyReferenceYearBasis } from "../api/types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { useRubric, useSetCitationRecency } from "./useRubric";

const RECENCY_SETTING_COPY = {
  heading: "Citation recency note",
  intro: "Notes each reference older than a window you set. It describes source age only; an older source can still be the right one to cite.",
  enabled: "Note sources older than the window",
  window: "Window (years)",
  windowInvalid: "Enter a whole number of years from 1 to 100.",
  basis: "Count back from",
  basisManuscript: "The manuscript's year, from its title page (the check date if none is found)",
  basisCheck: "The date of the check",
  counts: "Count these notes toward readiness",
  fromDefault: "default",
  fromFormat: "set for this format",
  changed: "changed, not saved",
  save: "Save recency setting",
  saved: "Saved. New checks use this setting; existing reports keep the values they were checked with.",
  useDefaults: "Use the defaults",
  invalidTitle: "Recency setting needs saving",
  invalid: "This format's saved recency setting is no longer valid, so checks report recency as not assessed. Save a setting to fix it.",
  nothingSaved: "Nothing was saved.",
} as const;

type Draft = Pick<CitationRecencySettingsOut, "enabled" | "window_years" | "reference_year_basis" | "counts_toward_readiness">;
const FIELDS = ["enabled", "window_years", "reference_year_basis", "counts_toward_readiness"] as const;

const draftOf = (settings: CitationRecencySettingsOut): Draft => ({
  enabled: settings.enabled, window_years: settings.window_years,
  reference_year_basis: settings.reference_year_basis, counts_toward_readiness: settings.counts_toward_readiness,
});

// Only what the instructor changed is sent, so the other values keep
// following the defaults.
function changes(draft: Draft, settings: CitationRecencySettingsOut): CitationRecencySettingsIn {
  const body: Record<string, unknown> = {};
  // Turned off, the other values are hidden and not saved.
  if (!draft.enabled) return draft.enabled !== settings.enabled ? { enabled: false } : {};
  for (const field of FIELDS) {
    if (draft[field] !== settings[field]) body[field] = draft[field];
  }
  return body as CitationRecencySettingsIn;
}

export function CitationRecencySetting({ familyId, rubricId }: { familyId: string; rubricId: number }) {
  const rubric = useRubric(rubricId);
  const save = useSetCitationRecency();
  // The server's answer to a save is the newest word on where each value
  // comes from, ahead of the format record's refetch.
  const [answered, setAnswered] = useState<CitationRecencySettingsOut>();
  const settings = answered ?? rubric.data?.citation_recency ?? undefined;
  const [draft, setDraft] = useState<Draft | undefined>(() => settings && draftOf(settings));
  const [saved, setSaved] = useState(false);
  const headingId = useId();
  const windowId = useId();
  const windowErrorId = useId();
  const basisId = useId();
  const refusalId = useId();
  const windowRef = useRef<HTMLInputElement>(null);
  const saveRef = useRef<HTMLButtonElement>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const windowRefusal = fieldRefusal(save.error, "window_years");
  useRefusalFocus(windowRefusal, windowRef);
  useEffect(() => { if (settings && !draft) setDraft(draftOf(settings)); }, [settings, draft]);
  if (!settings || !draft) return null;

  const source = (field: keyof Draft) => draft[field] !== settings[field] ? RECENCY_SETTING_COPY.changed
    : settings.setting_sources[field] === "format" ? RECENCY_SETTING_COPY.fromFormat : RECENCY_SETTING_COPY.fromDefault;
  const otherRefusal = formRefusal(save.error, ["window_years"]);
  const windowValid = Number.isInteger(draft.window_years) && draft.window_years >= 1 && draft.window_years <= 100;
  const body = changes(draft, settings);
  const changed = Object.keys(body).length > 0;

  function change(next: Partial<Draft>) {
    setSaved(false);
    save.reset();
    setDraft((current) => current && { ...current, ...next });
  }

  async function submit(next: CitationRecencySettingsIn) {
    setSaved(false);
    try {
      const out = await save.mutateAsync({ familyId, body: next });
      setAnswered(out);
      setDraft(draftOf(out));
      setSaved(true);
      // Save is now disabled and Use the defaults may be gone, so focus goes
      // to the confirmation instead of falling to the page.
      requestAnimationFrame(() => statusRef.current?.focus());
    } catch (error) {
      // A refused window takes focus itself; anything else returns to Save.
      // Read the refusal from this error: `save` here is the render the click
      // came from, whose error was still empty, so it would always send focus
      // to Save and could take it back from the window after the window had it.
      requestAnimationFrame(() => { if (!fieldRefusal(error, "window_years")) saveRef.current?.focus(); });
    }
  }

  const windowDescribedBy = [!windowValid ? windowErrorId : null, windowRefusal ? refusalId : null].filter(Boolean).join(" ") || undefined;
  return <section className="signal-studio-field signal-recency-setting" aria-labelledby={headingId}>
    <h3 id={headingId}>{RECENCY_SETTING_COPY.heading}</h3>
    <p>{RECENCY_SETTING_COPY.intro}</p>
    {settings.saved_setting_invalid && <Alert title={RECENCY_SETTING_COPY.invalidTitle} tone="warning">{RECENCY_SETTING_COPY.invalid}</Alert>}
    <label className="signal-recency-setting__check">
      <input type="checkbox" checked={draft.enabled} disabled={save.isPending} onChange={(event) => change({ enabled: event.target.checked })} />
      <span>{RECENCY_SETTING_COPY.enabled} <small>({source("enabled")})</small></span>
    </label>
    {draft.enabled && <>
      <label htmlFor={windowId}>{RECENCY_SETTING_COPY.window} <small>({source("window_years")})</small></label>
      <input ref={windowRef} id={windowId} type="number" min={1} max={100} step={1} inputMode="numeric"
        value={Number.isNaN(draft.window_years) ? "" : draft.window_years}
        disabled={save.isPending} aria-invalid={!windowValid || Boolean(windowRefusal) || undefined}
        aria-describedby={windowDescribedBy}
        onChange={(event) => change({ window_years: event.target.value === "" ? Number.NaN : Number(event.target.value) })} />
      {!windowValid && <p id={windowErrorId} className="signal-field-error">{RECENCY_SETTING_COPY.windowInvalid}</p>}
      {windowRefusal && <p id={refusalId} className="signal-field-error">{windowRefusal} {RECENCY_SETTING_COPY.nothingSaved}</p>}
      <label htmlFor={basisId}>{RECENCY_SETTING_COPY.basis} <small>({source("reference_year_basis")})</small></label>
      <select id={basisId} value={draft.reference_year_basis} disabled={save.isPending}
        onChange={(event) => change({ reference_year_basis: event.target.value as RecencyReferenceYearBasis })}>
        <option value="manuscript_year">{RECENCY_SETTING_COPY.basisManuscript}</option>
        <option value="check_date">{RECENCY_SETTING_COPY.basisCheck}</option>
      </select>
      <label className="signal-recency-setting__check">
        <input type="checkbox" checked={draft.counts_toward_readiness} disabled={save.isPending} onChange={(event) => change({ counts_toward_readiness: event.target.checked })} />
        <span>{RECENCY_SETTING_COPY.counts} <small>({source("counts_toward_readiness")})</small></span>
      </label>
    </>}
    <div className="signal-recency-setting__actions">
      <Button ref={saveRef} variant="secondary" busy={save.isPending} disabled={!changed || (draft.enabled && !windowValid)}
        onClick={() => submit(body)}>{RECENCY_SETTING_COPY.save}</Button>
      {Object.values(settings.setting_sources).includes("format") &&
        <Button variant="quiet" disabled={save.isPending} onClick={() => submit({})}>{RECENCY_SETTING_COPY.useDefaults}</Button>}
    </div>
    <p ref={statusRef} role="status" tabIndex={-1} className="signal-recency-setting__status">{saved ? RECENCY_SETTING_COPY.saved : ""}</p>
    {otherRefusal && <p role="alert" className="signal-field-error">{otherRefusal} {RECENCY_SETTING_COPY.nothingSaved}</p>}
    {save.isError && !isValidationRefusal(save.error) && <p role="alert" className="signal-field-error">
      {save.error instanceof ApiError && save.error.status >= 400 && save.error.status < 500 ? save.error.message : OUTCOME_UNKNOWN_COPY}</p>}
  </section>;
}
