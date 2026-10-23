// F8.10, screens 4h/4s/4w: no arbitrary URLs or private draft fields in history context.
import { AUDIT_RETURN as protocol, REVIEW_DESK_RETURN } from "../config/signalRoutes";
import { validDeskAction, validReviewDeskPath } from "./reviewDeskReturn";

export interface AuditReviewOrigin {
  returnPath: string;
  focusPath: string;
  elementId: string;
  fallbackElementId: string;
  scrollBlock?: "start";
}

function positive(value: string): boolean {
  return /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value));
}

function auditDeskPath(value: string): boolean {
  if (!validReviewDeskPath(value)) return false;
  const queryAt = value.indexOf("?");
  const params = new URLSearchParams(queryAt < 0 ? "" : value.slice(queryAt + 1));
  for (const [key, entry] of params) {
    if (params.getAll(key).length !== 1 || /[\\\p{Cc}]/u.test(entry)) return false;
    if (key === "queue") { if (!Object.hasOwn(protocol.deskQueues, entry)) return false; }
    else if (key === "sort") { if (!(protocol.deskSorts as readonly string[]).includes(entry)) return false; }
    else if (key === "page") { if (!positive(entry)) return false; }
    else if (key === "status") { if (!(Object.values(protocol.deskQueues) as readonly string[]).includes(entry)) return false; }
    else if (key === "needs_review") { if (entry !== "true" && entry !== "false") return false; }
    else if (key !== "q" && key !== "program") return false;
  }
  const queue = params.get("queue");
  if (queue) {
    if (params.has("status") && params.get("status") !== protocol.deskQueues[queue as keyof typeof protocol.deskQueues]) return false;
    if (params.has("needs_review") && (params.get("needs_review") === "true") !== (queue === "needs_review")) return false;
  }
  return true;
}

export function validAuditReviewOrigin(value: unknown, runId: number): AuditReviewOrigin | undefined {
  if (typeof value !== "string" || value.length > protocol.maxPathLength
    || !Number.isSafeInteger(runId) || runId <= 0 || /[\\\p{Cc}]/u.test(value)) return;
  const hashAt = value.indexOf("#");
  const focusPath = hashAt < 0 ? value : value.slice(0, hashAt);
  const hash = hashAt < 0 ? "" : value.slice(hashAt);
  const queryAt = focusPath.indexOf("?");
  const pathname = queryAt < 0 ? focusPath : focusPath.slice(0, queryAt);
  const match = protocol.routePattern.exec(pathname);
  if (!match || Number(match[1]) !== runId || !positive(match[1])) return;
  const params = new URLSearchParams(queryAt < 0 ? "" : focusPath.slice(queryAt + 1));
  for (const [key, entry] of params) {
    if (params.getAll(key).length !== 1 || /[\\\p{Cc}]/u.test(entry)) return;
    if ((protocol.positiveParams as readonly string[]).includes(key)) { if (!positive(entry)) return; }
    else if (key === "flags_clusters_open") {
      const identifiers = entry.split(",");
      if (!identifiers.every(positive) || new Set(identifiers).size !== identifiers.length) return;
    }
    else if (key === "criterion_record") { if (entry !== "all" && !positive(entry)) return; }
    else if (key === REVIEW_DESK_RETURN.pathParam) { if (!auditDeskPath(entry)) return; }
    else if (key === REVIEW_DESK_RETURN.focusParam) {
      if (!validDeskAction(entry) || !params.has(REVIEW_DESK_RETURN.pathParam)) return;
    } else {
      const allowed = Object.hasOwn(protocol.enums, key)
        ? protocol.enums[key as keyof typeof protocol.enums] as readonly string[] : undefined;
      if (!allowed?.includes(entry)) return;
    }
  }
  if (hash) {
    const criterion = protocol.criterionHashPattern.exec(hash);
    if (criterion) {
      if (!positive(criterion[1]) || Number(criterion[1]) !== runId || !positive(criterion[2])
        || params.get("criterion_record") !== criterion[2]) return;
    } else if (!(protocol.hashes as readonly string[]).includes(hash)) return;
    if (params.get("criterion_record") === "all" && hash !== "#criteria-results-heading") return;
  }
  // Keep the validated byte sequence: focus ownership includes query order.
  const action = `${protocol.actionPrefix}${runId}`;
  const elementId = hash ? hash.slice(1) : action;
  const fallbackElementId = hash ? action : "readiness-report-heading";
  return { returnPath: value, focusPath, elementId, fallbackElementId, scrollBlock: hash ? "start" : undefined };
}

export function auditDestination(runId: number, origin: { pathname: string; search: string; hash: string }): string {
  const params = new URLSearchParams({ check_run_id: String(runId) });
  const context = validAuditReviewOrigin(origin.pathname + origin.search + origin.hash, runId);
  if (context) params.set(protocol.pathParam, context.returnPath);
  return `/audit?${params.toString()}`;
}

export function readAuditReviewOrigin(search: string, runId: number): AuditReviewOrigin | undefined {
  const params = new URLSearchParams(search);
  if (params.getAll(protocol.pathParam).length !== 1 || params.getAll("check_run_id").length !== 1
    || params.get("check_run_id") !== String(runId)) return;
  return validAuditReviewOrigin(params.get(protocol.pathParam), runId);
}
