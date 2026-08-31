import type { ReadinessStatus } from "../api/types";
import { cx } from "../components/cx";

const BAND_LABELS: Record<ReadinessStatus, string> = {
  ready: "Ready",
  conditionally_ready: "Conditionally Ready",
  not_ready: "Not Ready",
  needs_review: "Needs Review",
};

export function ReadinessBand({ status, className }: { status: ReadinessStatus; className?: string }) {
  return (
    <span className={cx("signal-band", `signal-band--${status.replaceAll("_", "-")}`, className)}>
      <span className="signal-band__mark" aria-hidden="true" />
      {BAND_LABELS[status]}
    </span>
  );
}
