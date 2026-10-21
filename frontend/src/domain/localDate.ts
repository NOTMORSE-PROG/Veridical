/** Calendar dates use the instructor's browser locale and timezone. */
export function localDate(value: string): string {
  return new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
