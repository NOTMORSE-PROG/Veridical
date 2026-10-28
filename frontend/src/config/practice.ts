/** Public prepared-review hosting. Account data never passes through this URL. */
export const PRACTICE_WORKSPACE_URL = import.meta.env.VITE_PRACTICE_WORKSPACE_URL ?? "";

export function practiceDestination(
  configured: string,
  currentOrigin: string,
  pathname = "/demo",
  search = "",
  hash = "",
): string | null {
  try {
    const target = new URL(configured);
    if (target.protocol !== "https:" || target.username || target.password ||
        target.search || target.hash || target.origin === currentOrigin ||
        !/^\/demo\/?$/.test(target.pathname) ||
        !/^\/demo(?:\/|$)/.test(pathname)) return null;
    for (const segment of pathname.split("/")) {
      const decoded = decodeURIComponent(segment);
      if (decoded === "." || decoded === ".." || /[/\\%]/.test(decoded)) return null;
    }
    target.pathname = pathname;
    if (!/^\/demo(?:\/|$)/.test(target.pathname)) return null;
    target.search = search;
    target.hash = hash;
    return target.href;
  } catch {
    return null;
  }
}
