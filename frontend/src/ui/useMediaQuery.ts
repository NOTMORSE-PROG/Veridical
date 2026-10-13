// V-077: whether a CSS media query currently matches, so markup can follow
// the same breakpoint the stylesheet uses (for example, tab semantics only
// where tabs are actually shown). Falls back to `fallback` where
// `matchMedia` is unavailable (server render, test environment).
import { useEffect, useState } from "react";

export function useMediaQuery(query: string, fallback: boolean): boolean {
  const supported = typeof window !== "undefined" && typeof window.matchMedia === "function";
  const [matches, setMatches] = useState(() => (supported ? window.matchMedia(query).matches : fallback));

  useEffect(() => {
    if (!supported) return;
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query, supported]);

  return matches;
}
