import type { BackgroundUpload } from "./BackgroundUploads";

const isActive = (item: BackgroundUpload) => item.phase === "waiting" || item.phase === "sending" || item.phase === "preparing";
// Still in this tab's hands: closing the tab now loses the file.
const isSending = (item: BackgroundUpload) => item.phase === "waiting" || (item.phase === "sending" && !item.serverOwned);
const needsAttention = (item: BackgroundUpload) => item.phase === "failed" || (item.phase === "done" && Boolean(item.checkStartError));

// Folded, the tray keeps every warning the rows would have shown: a file
// still being sent, and anything that needs the instructor.
export function traySummary(uploads: BackgroundUpload[]): { text: string; attention: boolean }[] {
  const sending = uploads.filter(isSending).length;
  const preparing = uploads.filter((item) => isActive(item) && !isSending(item)).length;
  const attention = uploads.filter(needsAttention).length;
  const ready = uploads.filter((item) => item.phase === "done" && !needsAttention(item)).length;
  const parts: { text: string; attention: boolean }[] = [];
  if (sending) parts.push({ text: `${sending} sending: keep this tab open`, attention: true });
  if (attention) parts.push({ text: `${attention} need${attention === 1 ? "s" : ""} attention`, attention: true });
  if (preparing) parts.push({ text: `${preparing} preparing`, attention: false });
  if (ready) parts.push({ text: `${ready} ready`, attention: false });
  return parts;
}
