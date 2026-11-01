// BUG-677: the similar passages the originality check decided not to raise,
// by reason, and the standard texts it named. They were checked and set aside,
// so they are listed here and never as something the check did not cover.
import type { OriginalityScope } from "../api/types";

type Row = { key: string; label: string; count: number | null | undefined; unit: [string, string]; text: string };

export function OriginalitySetAside({ scope }: { scope: OriginalityScope }) {
  const rows: Row[] = [
    { key: "cited", label: "Cited quotation", count: scope.n_set_aside_cited_quotation, unit: ["passage", "passages"],
      text: "The only shared wording was a marked, cited quotation." },
    { key: "standard", label: "Standard wording", count: scope.n_set_aside_standard_text, unit: ["passage", "passages"],
      text: "The only shared wording was a standard text many papers reuse." },
    { key: "weak", label: "Weak section match", count: scope.n_set_aside_weak_section_evidence, unit: ["section", "sections"],
      text: "The only evidence was a passage too weak to count as a match." },
    { key: "none", label: "No shared wording", count: scope.n_set_aside_no_shared_wording, unit: ["passage", "passages"],
      text: "Similar in topic or layout, with no wording in common." },
  ];
  const recorded = rows.filter(row => typeof row.count === "number");
  const shown = recorded.filter(row => (row.count ?? 0) > 0);
  const names = scope.standard_texts_set_aside ?? [];
  const bothKinds = shown.some(row => row.key === "cited") && shown.some(row => row.key === "standard");
  return <section className="signal-originality-scope signal-originality-set-aside" aria-labelledby="originality-set-aside-heading">
    <h3 id="originality-set-aside-heading">Similar passages not raised</h3>
    {!recorded.length ? <p>Not recorded for this run.</p>
      : !shown.length ? <p>None were set aside.</p>
        : <>
          <p>These were set aside. They are not findings and not evidence of originality. Only the counts were saved; the passages are not listed.</p>
          <dl className="signal-fact-rows">
            {shown.map(row => <div key={row.key}><dt>{row.label}</dt><dd>
              <span className="signal-originality-set-aside__count">{row.count}</span>
              <div>{row.count === 1 ? row.unit[0] : row.unit[1]}. {row.text}
                {row.key === "standard" && (names.length
                  ? <ul className="signal-originality-set-aside__names">{names.map(name => <li key={name}>{name}</li>)}</ul>
                  : <span className="signal-field-hint"> Not named in this record.</span>)}
              </div>
            </dd></div>)}
          </dl>
          {bothKinds && <p className="signal-field-hint">One passage can count under both Cited quotation and Standard wording.</p>}
        </>}
    {recorded.length > 0 && recorded.length < rows.length && <p className="signal-field-hint">Some reasons were not recorded for this run.</p>}
  </section>;
}

/** On a raised originality finding, right under the shading legend it
 * qualifies: standard wording the check did not count when judging it. */
export function StandardWordingLeftOut({ names }: { names?: string[] }) {
  if (!names?.length) return null;
  return <dl className="signal-fact-rows signal-standard-wording-left-out"><div><dt>Standard wording not counted</dt><dd>
    <ul>{names.map(name => <li key={name}>{name}</li>)}</ul>
    <span className="signal-field-hint">Shading shows all shared wording, including this standard wording. VERIDICAL did not count the standard wording.</span>
  </dd></div></dl>;
}
