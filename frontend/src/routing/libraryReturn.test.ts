import { describe, expect, it } from "vitest";
import { LIBRARY_RETURN as protocol } from "../config/signalRoutes";
import {
  librarySourceDestination, readLibraryComparisonState, readLibraryReturn, validLibraryPath,
} from "./libraryReturn";

const path = "/library?q=%2327%2B%26%25&program=IT&page=2";
const state = { comparisonMode: true, comparisonSelection: [27, 13] };
const location = { pathname: "/library", search: path.slice("/library".length) };
const destination = () => new URL(librarySourceDestination(27, location, state), "http://localhost");

describe("Library-only source return protocol", () => {
  it("retains the exact accepted URL encoding/order and neutral comparison fields", () => {
    expect(validLibraryPath(path)).toBe(path);
    const url = destination();
    expect(readLibraryReturn(url)).toEqual({
      returnPath: path, elementId: "library-view-27", ...state,
    });
    expect(url.searchParams.get(protocol.pathParam)).toBe(path);
    expect(url.search).not.toMatch(/title|author|filename|ownership/);
  });

  it.each([
    "/library", "/library?q=", "/library?q=x?y", "/library?q=%E6%96%87%E7%8C%AE", "/library?program=__unset__&page=1",
  ])("accepts supported list semantics: %s", (value) => expect(validLibraryPath(value)).toBe(value));

  it.each([
    "https://example.com/library", "//example.com/library", "/library/", "/library/27", "/dashboard?q=x",
    "/library#heading", "/library?scope=pending", "/library?compareFrom=27", "/library?q=a&q=b",
    "/library?program=IT&program=CS", "/library?page=1&page=2", "/library?page=0", "/library?page=-1",
    "/library?page=1.2", "/library?page=1e2", "/library?page=9007199254740992",
    "/library?q=bad\\path", "/library?q=bad%5Cpath", "/library?q=bad%0Avalue", "/library?program=" + "a".repeat(51),
    "/library?q=" + "a".repeat(protocol.maxPathLength),
    "/library??q=x", "/library??program=IT", "/library??page=2",
  ])("rejects unrelated or ambiguous list context: %s", (value) => expect(validLibraryPath(value)).toBeUndefined());

  it.each([
    { comparisonMode: true, comparisonSelection: [] },
    { comparisonMode: true, comparisonSelection: [27] },
    state,
    { comparisonMode: false, comparisonSelection: [] },
  ])("round-trips explicit mode independently of selection: %j", (comparison) => {
    const url = new URL(librarySourceDestination(27, location, comparison), "http://localhost");
    expect(readLibraryReturn(url)).toMatchObject(comparison);
  });

  it.each([
    { comparisonMode: "open", comparisonSelection: [] },
    { comparisonMode: false, comparisonSelection: [27] },
    { comparisonSelection: [27, 27] }, { comparisonSelection: [1, 2, 3] },
    { comparisonSelection: [-1] }, { comparisonSelection: [0] },
    { comparisonSelection: [1.5] }, { comparisonSelection: [Number.MAX_SAFE_INTEGER + 1] },
    { comparisonSelection: ["27"] }, { comparisonSelection: {} },
    { comparisonMode: null }, { comparisonSelection: null }, { comparisonMode: true, comparisonSelection: Array(1) },
  ])("rejects malformed history comparison snapshots: %j", (value) => {
    expect(readLibraryComparisonState(value)).toBeUndefined();
  });

  it("keeps legacy Change selection compatible without granting availability", () => {
    expect(readLibraryComparisonState({ comparisonSelection: [27, 13] })).toEqual(state);
  });

  it.each(Object.values({
    path: protocol.pathParam, origin: protocol.originParam, mode: protocol.modeParam, selection: protocol.selectionParam,
  }))("rejects a missing or duplicate outer %s field", (key) => {
    const missing = destination(); missing.searchParams.delete(key);
    expect(readLibraryReturn(missing)).toBeUndefined();
    const duplicate = destination(); duplicate.searchParams.append(key, duplicate.searchParams.get(key)!);
    expect(readLibraryReturn(duplicate)).toBeUndefined();
  });

  it.each([
    [protocol.pathParam, "//example.com/library"], [protocol.pathParam, "/library?q=x&q=y"],
    [protocol.originParam, "13"], [protocol.modeParam, "true"], [protocol.selectionParam, "27,27"],
    [protocol.selectionParam, "27,13,1"], [protocol.selectionParam, "1e2"], [protocol.selectionParam, "-1"],
  ])("rejects a malformed entire packet (%s=%s)", (key, value) => {
    const url = destination(); url.searchParams.set(key, value);
    expect(readLibraryReturn(url)).toBeUndefined();
  });

  it("rejects oversized context and packets on unrelated routes", () => {
    expect(readLibraryReturn({ pathname: "/library/27", search: "?" + "x".repeat(protocol.maxContextLength) })).toBeUndefined();
    expect(readLibraryReturn({ pathname: "/report/27", search: destination().search })).toBeUndefined();
  });
});
