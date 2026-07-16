// Minimal class joiner. Variant maps stay full literal strings because
// Tailwind's scanner only sees classes written out whole — dynamically
// composed names get dropped from the build (V-002 edge case).
export function cx(...parts: Array<string | false | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
