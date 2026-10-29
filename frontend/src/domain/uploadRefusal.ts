// BUG-546: after the server refuses a prepared file, say whether a record of
// the attempt was kept. A kept record's state is not known here (it may have
// finished, or been removed since), so the wording does not guess it.
export const UPLOAD_REFUSAL_COPY = {
  kept: "A record of this upload was kept. Open it in Review Desk to see its state.",
  openDesk: "Open Review Desk",
  notKept: "No record of this upload was saved.",
} as const;
