// Shared text-formatting helpers — extracted from Report.tsx (BUG-033)
// so FlagsPanel.tsx doesn't fork a second copy of the same truncation
// logic.
export function truncateAtWord(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > max * 0.6 ? cut.slice(0, lastSpace) : cut) + "…";
}
