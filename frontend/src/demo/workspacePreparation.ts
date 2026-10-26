import { reviewTasks, sampleFlags, WORKSPACE_SAMPLE } from "./workspaceFixture";

type SourceStage = "ingesting" | "structural" | "integrity" | "aggregating";

// These stages do real source-loading and binding work. No clocks or model
// responses are used to invent pipeline activity in the temporary workspace.
export async function prepareWorkspaceSource(fetchSource: typeof fetch, signal: AbortSignal, stage: (key: SourceStage, note: string) => void) {
  stage("ingesting", "Loading the complete published PDF and verifying its checksum.");
  const original = await fetchSource(WORKSPACE_SAMPLE.pdfAsset, { credentials: "omit", signal });
  if (!original.ok) throw new Error("The original manuscript could not be loaded.");
  const bytes = await original.arrayBuffer();
  signal.throwIfAborted();
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), value => value.toString(16).padStart(2, "0")).join("");
  if (digest !== WORKSPACE_SAMPLE.sha256) throw new Error("The manuscript checksum does not match the published original.");

  stage("structural", "Checking the stored page index against all 109 original pages.");
  const pageResponse = await fetchSource("/sample-assets/manuscript-text.json", { credentials: "omit", signal });
  if (!pageResponse.ok) throw new Error("The manuscript page index could not be loaded.");
  const pages: unknown = await pageResponse.json();
  signal.throwIfAborted();
  if (!Array.isArray(pages) || pages.length !== WORKSPACE_SAMPLE.pageCount || !pages.every((page, index) => page?.page === index + 1 && typeof page.text === "string")) throw new Error("The manuscript page index is incomplete.");

  stage("integrity", "Binding the recorded observations and rubric excerpts to their original pages.");
  const excerpts = [...sampleFlags.map(flag => ({ page: Number(flag.page_anchor.match(/\d+/)?.[0]), quote: flag.evidence_excerpt })), ...reviewTasks];
  for (const excerpt of excerpts) {
    signal.throwIfAborted();
    if (!pages[excerpt.page - 1]?.text.replace(/\s+/g, " ").includes(excerpt.quote.replace(/\s+/g, " "))) throw new Error("A review excerpt could not be matched to its original page.");
  }
  stage("aggregating", "Source records verified. Keeping instructor judgments and decisions intact.");
  return { pageCount: pages.length, excerptsBound: excerpts.length, sha256: digest };
}
