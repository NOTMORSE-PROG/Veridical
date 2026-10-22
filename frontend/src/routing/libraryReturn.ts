// V-081 / BUG-428, screens 4z/4x: reloadable Library source return context.
import { LIBRARY_RETURN as protocol } from "../config/signalRoutes";
import type { ReviewLocation } from "./reviewDeskReturn";

export interface LibraryComparisonState {
  comparisonMode: boolean;
  comparisonSelection: number[];
}

export interface LibraryReturnContext extends LibraryComparisonState {
  returnPath: string;
  elementId: string;
}

const invalidCharacters = /[\\\p{Cc}]/u;
const positiveInteger = /^[1-9]\d*$/;

export function validLibraryRecordId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

export function libraryStateRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

/** Legacy comparisonSelection remains supported, including Change selection. */
export function readLibraryComparisonState(value: unknown): LibraryComparisonState | undefined {
  const state = libraryStateRecord(value);
  if (state.comparisonMode === undefined && state.comparisonSelection === undefined) return undefined;
  const raw = state.comparisonSelection === undefined ? [] : state.comparisonSelection;
  if (!Array.isArray(raw) || raw.length > protocol.selectionLimit) return undefined;
  const selected = [...raw];
  if (!selected.every(validLibraryRecordId) || new Set(selected).size !== selected.length) return undefined;
  const mode = state.comparisonMode === undefined ? selected.length > 0 : state.comparisonMode;
  if (typeof mode !== "boolean" || (!mode && selected.length > 0)) return undefined;
  return { comparisonMode: mode, comparisonSelection: [...selected] };
}

/** Validate parsed semantics, then preserve the original accepted encoding/order. */
export function validLibraryPath(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > protocol.maxPathLength
    || value.includes("#") || invalidCharacters.test(value)) return undefined;
  const question = value.indexOf("?");
  const pathname = question < 0 ? value : value.slice(0, question);
  if (pathname !== protocol.pathname) return undefined;
  const params = new URLSearchParams(question < 0 ? "" : value.slice(question));
  for (const [key, decoded] of params) {
    if (!(protocol.queryKeys as readonly string[]).includes(key)
      || params.getAll(key).length !== 1 || invalidCharacters.test(decoded)) return undefined;
    if (key === "page" && (!positiveInteger.test(decoded) || !Number.isSafeInteger(Number(decoded)))) return undefined;
    if (key === "program" && decoded.length > protocol.maxProgramLength) return undefined;
  }
  return question === value.length - 1 ? pathname : value;
}

export function libraryCardActionId(recordId: number): string {
  return protocol.actionPrefix + recordId;
}

export function librarySourceDestination(
  recordId: number, location: ReviewLocation, comparison: LibraryComparisonState,
): string {
  if (!validLibraryRecordId(recordId)) return protocol.pathname;
  const destination = `${protocol.pathname}/${recordId}`;
  const path = validLibraryPath(location.pathname + location.search);
  const state = readLibraryComparisonState(comparison);
  if (!path || !state) return destination;
  const params = new URLSearchParams({
    [protocol.pathParam]: path,
    [protocol.originParam]: String(recordId),
    [protocol.modeParam]: state.comparisonMode ? protocol.openMode : protocol.closedMode,
    [protocol.selectionParam]: state.comparisonSelection.join(","),
  });
  return destination + "?" + params.toString();
}

/** Reject a malformed packet as a whole; context never establishes authority. */
export function readLibraryReturn(location: ReviewLocation): LibraryReturnContext | undefined {
  if (location.search.length > protocol.maxContextLength) return undefined;
  const destination = protocol.sourcePattern.exec(location.pathname);
  if (!destination || destination[0] !== location.pathname) return undefined;
  const params = new URLSearchParams(location.search);
  const keys = [protocol.pathParam, protocol.originParam, protocol.modeParam, protocol.selectionParam];
  if (keys.some((key) => params.getAll(key).length !== 1)) return undefined;
  const returnPath = validLibraryPath(params.get(protocol.pathParam));
  const origin = params.get(protocol.originParam)!;
  if (!returnPath || origin !== destination[1] || !validLibraryRecordId(Number(origin))) return undefined;
  const mode = params.get(protocol.modeParam);
  if (mode !== protocol.openMode && mode !== protocol.closedMode) return undefined;
  const selection = params.get(protocol.selectionParam)!;
  const pieces = selection === "" ? [] : selection.split(",");
  if (pieces.some((piece) => !positiveInteger.test(piece))) return undefined;
  const state = readLibraryComparisonState({
    comparisonMode: mode === protocol.openMode, comparisonSelection: pieces.map(Number),
  });
  return state ? { ...state, returnPath, elementId: libraryCardActionId(Number(origin)) } : undefined;
}
