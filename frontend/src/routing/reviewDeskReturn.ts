// V-081 / BUG-281 (F8, screens 4e/4h/4w): validated, reloadable list context.
import { createPath, parsePath, type Path, type To } from "react-router";
import { REVIEW_DESK_RETURN as protocol } from "../config/signalRoutes";

export interface ReviewLocation {
  pathname: string;
  search: string;
}

export interface ReviewDeskReturn {
  returnPath: string;
  elementId?: string;
}

export function validReviewDeskPath(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > protocol.maxPathLength
    || /[\\#\p{Cc}]/u.test(value)) return undefined;
  const question = value.indexOf("?");
  const path = question < 0 ? value : value.slice(0, question);
  if (path !== protocol.pathname) return undefined;
  return question === value.length - 1 ? path : value;
}

export function validDeskAction(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const match = protocol.actionPattern.exec(value);
  return match && match[0] === value && Number.isSafeInteger(Number(match[1])) ? value : undefined;
}

export function isReviewWorkflowPath(value: string): boolean {
  return protocol.workflowPattern.exec(value)?.[0] === value;
}

export function readReviewDeskReturn(location: ReviewLocation): ReviewDeskReturn | undefined {
  const params = new URLSearchParams(location.search);
  if (params.getAll(protocol.pathParam).length !== 1) return undefined;
  const returnPath = validReviewDeskPath(params.get(protocol.pathParam));
  if (!returnPath) return undefined;
  return {
    returnPath,
    elementId: params.getAll(protocol.focusParam).length === 1
      ? validDeskAction(params.get(protocol.focusParam)) : undefined,
  };
}

export function reviewDeskDestination(to: string, location: ReviewLocation, sourceId?: string): string;
export function reviewDeskDestination(to: To, location: ReviewLocation, sourceId?: string): To;
export function reviewDeskDestination(to: To, location: ReviewLocation, sourceId?: string): To {
  const parts: Partial<Path> = typeof to === "string" ? parsePath(to) : { ...to };
  const pathname = parts.pathname ?? location.pathname;
  if (!isReviewWorkflowPath(pathname)) return to;
  const returnPath = location.pathname === protocol.pathname
    ? validReviewDeskPath(location.pathname + location.search) : undefined;
  const context = returnPath ? { returnPath, elementId: validDeskAction(sourceId) }
    : readReviewDeskReturn(location);
  if (!context) return to;
  const params = new URLSearchParams(parts.search);
  const existing = readReviewDeskReturn({ pathname, search: parts.search ?? "" });
  // Focus registrations compare full paths. Keep a matching parent address
  // byte-for-byte, including query order and existing percent encoding.
  if (existing?.returnPath === context.returnPath
    && existing.elementId === context.elementId
    && (context.elementId !== undefined || !params.has(protocol.focusParam))) return to;
  params.set(protocol.pathParam, context.returnPath);
  if (context.elementId) params.set(protocol.focusParam, context.elementId);
  else params.delete(protocol.focusParam);
  const target = { ...parts, pathname, search: `?${params.toString()}` };
  return typeof to === "string" ? createPath(target) : target;
}
