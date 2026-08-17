// BUG-049: fake-LLM-mode fixture data (a canned vision-pass table, a
// fabricated statistical-forensics flag with a real page anchor) used to
// render identically to a genuine finding everywhere a verdict is shown.
// Rendered wherever `llm_mode` reaches the frontend: the report, the flag
// evidence page, and the public adviser view. The exported PDF carries its
// own equivalent disclosure (backend/app/report/export.py) since it's a
// separate artifact this component can't reach.
//
// `backend-critic` finding (live-reproduced against the real dev DB, the
// same run the bug ticket itself was written against): a run that predates
// migration 0024 backfills to "unknown", and treating that the same as
// "real" -- i.e. showing nothing -- silently recreates the exact
// non-disclosure this ticket exists to close, for every run that already
// exists. "unknown" gets its own honest, distinct disclosure: not a
// confident "this is fake" claim (we don't know that), but not silence
// either (charter rule 9 -- N/A is not "passed").
export function TestModeBanner({ llmMode }: { llmMode: "fake" | "real" | "unknown" }) {
  if (llmMode === "real") return null;
  if (llmMode === "unknown") {
    return (
      <p
        role="status"
        className="rounded-lg border border-status-caution-text/25 bg-status-caution-bg px-4 py-2.5 text-sm font-medium text-status-caution-text"
      >
        AI mode unknown: this run predates AI-mode tracking, so whether it used real or
        simulated AI results can't be confirmed.
      </p>
    );
  }
  return (
    <p
      role="alert"
      className="rounded-lg border border-status-danger-text/25 bg-status-danger-bg px-4 py-2.5 text-sm font-medium text-status-danger-text"
    >
      Test-mode run: AI results are simulated and do not describe this document.
    </p>
  );
}
