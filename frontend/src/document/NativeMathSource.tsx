// F1/F6, source view 4w: presentation gaps never become manuscript evidence.
import type { NativeMathSourceOut, NativeMathSourcePieceOut } from "../api/types";

export function NativeMathPieces({ pieces }: { pieces: NativeMathSourcePieceOut[] }) {
  return <>{pieces.map((piece, index) => piece.kind === "text"
    ? <span key={index}>{piece.text}</span>
    : <span key={index}> [Native Word equation not displayed] </span>)}</>;
}

export function nativeMathInventoryLabel(source?: NativeMathSourceOut | null): string {
  if (source?.state === "complete" && source.occurrence_count !== null) {
    const count = source.occurrence_count;
    return `${count} native Word equation ${count === 1 ? "occurrence" : "occurrences"}`;
  }
  return "Native Word equation inventory unavailable";
}

export function NativeMathInventoryNote({ source }: { source?: NativeMathSourceOut | null }) {
  if (source?.state === "complete") return null;
  return <p className="signal-document-reconstruction-note">
    {source?.state === "unavailable"
      ? "The native Word equation inventory could not be verified against this source. Their presence and count are unknown."
      : "This saved extraction has no native Word equation inventory. Their presence and count are unknown."}
    {" "}This record does not establish that native Word equations were checked.
  </p>;
}
