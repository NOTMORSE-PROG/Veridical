// BUG-097 (presentation-only remedy, owner ruling 2026-08-24): a distinct,
// visible disclosure that this F7 originality/reuse match (or matches) comes
// from an instructor account's first-ever manuscript upload -- there is no
// earlier check on the account to weigh it against yet. Never changes
// severity or scoring (see backend/app/checks/reuse/query.py's
// is_first_upload_for_instructor docstring) -- presentation only.
//
// Two exports for two contexts, not one component with a branch: FlagDetail
// shows exactly one flag (singular copy, page-level banner slot, same
// bordered-pill treatment as TestModeBanner -- sits in the SAME slot and
// stacks with it when both apply, see FlagDetail.tsx). FlagsPanel's flags
// list can show several F7 matches from one run at once, and first_upload is
// computed ONCE per run and applied uniformly to every match in it (never
// mixed within one originality_reuse group) -- so one plural note scoped to
// just that group is accurate, and keeps a citation-integrity or
// statistical-forensics flag two rows away from inheriting a caveat that
// doesn't apply to it.
//
// `info` tone (not `caution`, which TestModeBanner's "unknown" state and
// RubricNeedsReviewBanner both use): `caution` is reserved for the ambiguous
// middle case a human must judge (Conditionally Ready, Medium severity).
// This is a neutral, factual disclosure -- "no track record yet" -- and
// giving it `caution`'s visual weight would imply extra uncertainty or
// severity, exactly what the owner rejected when declining to touch
// severity for this case. `info` is this codebase's established tone for
// neutral procedural disclosure (FlagDetail's own override-outcome banner,
// Report.tsx's verdict-rationale box, AdviserView.tsx, AuditLog.tsx).
export function FirstUploadContextBanner({ active }: { active: boolean }) {
  if (!active) return null;
  return (
    <p
      role="status"
      className="rounded-lg border border-status-info-text/25 bg-status-info-bg px-4 py-2.5 text-sm font-medium text-status-info-text"
    >
      First-ever check on this account: there is no earlier result yet to compare this match
      against, so verify the evidence below yourself before relying on it.
    </p>
  );
}

// Flush-list variant for FlagsPanel's accordion (every group/row divides
// with `border-t border-border`, no independent card border/rounding) --
// dropping the bordered-pill treatment above there would render as a
// floating card interrupting a flush list, not "reusing the visual
// language." Same `info` tint carried by background/text only.
export function FirstUploadContextGroupNote() {
  return (
    <p
      role="status"
      className="border-t border-border bg-status-info-bg px-3.5 py-2.5 text-sm font-medium text-status-info-text"
    >
      First-ever check on this account: the originality and reuse matches below have no earlier
      result from this account to compare against, so verify each one yourself before relying on
      it as evidence.
    </p>
  );
}
