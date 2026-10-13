// V-077: status of an own Library record that is not a comparison source
// yet. Reuses the process-status shape (label plus mark, WCAG 1.4.1) and
// never the readiness band colours: the Library makes no judgment.
import type { LibraryEntryStatus as Status } from "../api/types";

const LABELS: Record<Status, string> = {
  processing: "Reading file",
  needs_decision: "Possible duplicate",
  failed: "Could not read",
  active: "In the Library",
  removed: "Removed",
};

const TONES: Record<Status, string> = {
  processing: "running",
  needs_decision: "attention",
  failed: "attention",
  active: "done",
  removed: "cancelled",
};

export function LibraryEntryStatus({ status }: { status: Status }) {
  return (
    <span className={`signal-process-status signal-process-status--${TONES[status]}`}>
      <span className="signal-process-status__mark" aria-hidden="true" />
      {LABELS[status]}
    </span>
  );
}
