// BUG-167: client-side "have I opened this flag's evidence" tracking. Not
// a backend/audit concept -- purely a per-instructor convenience so a
// long report's coverage is visible without memory, on a device that
// preserves it.
//
// Instructor-scoped IN THE STORAGE KEY ITSELF (not a shared key with an
// instructor id inside its value): `frontend/src/auth/useAuth.ts` already
// documents this codebase being bitten twice by shared-machine
// cross-instructor leakage (BUG-183, BUG-009, BUG-036). A flat
// flag-id -> viewed map with no instructor scoping would recreate that
// bug in a new, particularly misleading form on a shared lab PC:
// Instructor B signing in would see Instructor A's "already reviewed"
// markers on flags B has never opened -- a FALSE coverage signal, worse
// than no signal at all (the same overreliance failure mode ground rule 1
// warns about, applied to a UI affordance instead of an AI explanation).
// Scoping by key means no explicit sign-out handling is needed for
// correctness: a different instructor id simply reads/writes a different
// key than this one ever touched.
import { useEffect, useState } from "react";

const STORAGE_KEY_PREFIX = "veridical.flags-viewed.v1";
const CHANGE_EVENT = "veridical:flags-viewed-change";
const EMPTY_VIEWED: ReadonlySet<number> = new Set();

function storageKey(instructorId: number): string {
  return `${STORAGE_KEY_PREFIX}.${instructorId}`;
}

function readViewedIds(instructorId: number | undefined): ReadonlySet<number> {
  if (!instructorId) return EMPTY_VIEWED;
  try {
    const raw = window.localStorage.getItem(storageKey(instructorId));
    if (!raw) return EMPTY_VIEWED;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? new Set(parsed.filter((n): n is number => Number.isInteger(n)))
      : EMPTY_VIEWED;
  } catch {
    // Storage disabled (private browsing) or a corrupt value: degrade to
    // "nothing marked", never throw -- this is a convenience affordance,
    // never a correctness record.
    return EMPTY_VIEWED;
  }
}

export function markFlagViewed(instructorId: number | undefined, flagId: number): void {
  if (!instructorId) return;
  const current = readViewedIds(instructorId);
  if (current.has(flagId)) return;
  try {
    window.localStorage.setItem(storageKey(instructorId), JSON.stringify([...current, flagId]));
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  } catch {
    // Quota exceeded or storage disabled: silently drop the write. There
    // is no useful recovery action to show the instructor for this.
  }
}

export function useViewedFlagIds(instructorId: number | undefined): ReadonlySet<number> {
  const [ids, setIds] = useState<ReadonlySet<number>>(() => readViewedIds(instructorId));
  useEffect(() => {
    setIds(readViewedIds(instructorId));
    function onChange() {
      setIds(readViewedIds(instructorId));
    }
    window.addEventListener(CHANGE_EVENT, onChange); // same-tab writes
    window.addEventListener("storage", onChange); // other tabs/windows
    return () => {
      window.removeEventListener(CHANGE_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, [instructorId]);
  return ids;
}
