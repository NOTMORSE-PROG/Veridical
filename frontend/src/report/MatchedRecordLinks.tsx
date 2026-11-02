// BUG-674: one link per Library record a finding matched, in match order,
// the same on the finding page and in manuscript review.
import { MATCHED_RECORDS_SHOWN } from "../domain/matchedRecords";
import { ActionLink } from "../ui/ActionLink";

export function MatchedRecordLinks({ records }: { records: number[] }) {
  if (!records.length) return null;
  if (records.length === 1) return <ActionLink to={`/library/${records[0]}`} variant="secondary">Open Library record #{records[0]}</ActionLink>;
  const more = records.length - MATCHED_RECORDS_SHOWN;
  return <>
    <p className="signal-matched-records__count">{records.length} matching Library records</p>
    <ul className="signal-matched-records" aria-label="Matching Library records">
      {records.slice(0, MATCHED_RECORDS_SHOWN).map(ref => <li key={ref}><ActionLink to={`/library/${ref}`} variant="secondary">Open Library record #{ref}</ActionLink></li>)}
    </ul>
    {more > 0 && <p className="signal-field-hint">and {more} more matching {more === 1 ? "record" : "records"}</p>}
  </>;
}
