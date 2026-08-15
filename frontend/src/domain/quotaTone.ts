// Shared quota-remaining -> tone/percent derivation (AppShell's QuotaChip,
// Settings' fuller quota view, V-042) -- one place decides the 15%/40%
// tier thresholds so the header chip and the Settings screen's richer
// view can never silently drift into disagreeing about "how bad is this".
export type QuotaTone = "neutral" | "caution" | "danger";

export interface QuotaRemaining {
  remainingPct: number;
  atZero: boolean;
  tone: QuotaTone;
}

export function quotaRemaining(callsUsed: number, dailyLimit: number): QuotaRemaining {
  const remainingPct =
    dailyLimit > 0 ? Math.max(0, Math.round(100 - (callsUsed / dailyLimit) * 100)) : 100;
  const atZero = remainingPct === 0;
  const tone: QuotaTone = remainingPct < 15 ? "danger" : remainingPct < 40 ? "caution" : "neutral";
  return { remainingPct, atZero, tone };
}
