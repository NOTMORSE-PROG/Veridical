// BUG-319: format uploaded file sizes in a non-zero unit under 1 MB so a
// several-kilobyte rubric or manuscript never reads as "0.0 MB" (which a
// first-time instructor misread as an empty or broken file).
const BYTES_PER_KB = 1024;
const BYTES_PER_MB = BYTES_PER_KB * BYTES_PER_KB;

export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 KB";
  }
  const mb = bytes / BYTES_PER_MB;
  if (mb >= 1) {
    return `${mb.toFixed(1)} MB`;
  }
  return `${Math.max(1, Math.round(bytes / BYTES_PER_KB))} KB`;
}
