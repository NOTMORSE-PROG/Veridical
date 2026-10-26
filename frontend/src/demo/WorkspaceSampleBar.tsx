import { WORKSPACE_SAMPLE, isWorkspaceSample } from "./workspaceFixture";
import "./workspaceSample.css";

export function WorkspaceSampleBar() {
  if (!isWorkspaceSample()) return null;
  return <aside className="signal-theme workspace-sample-bar" aria-label="Sample workspace">
    <div><strong>Sample workspace</strong><span>Real app screens · Original {WORKSPACE_SAMPLE.pageCount}-page capstone · Prepared review data</span></div>
    <div><a href={WORKSPACE_SAMPLE.pdfAsset} download={WORKSPACE_SAMPLE.filename}>Original PDF</a><a href={WORKSPACE_SAMPLE.source} target="_blank" rel="noreferrer">Source &amp; CC BY attribution</a><button type="button" onClick={() => { if (window.confirm("Reset only this tab's sample judgments and decisions?")) { sessionStorage.removeItem(WORKSPACE_SAMPLE.storageKey); window.location.assign("/demo/dashboard"); } }}>Reset sample</button><a href="/signin">Exit sample</a></div>
    <p>{WORKSPACE_SAMPLE.authors.join(", ")} (2025). Original PDF unchanged. Practice changes stay in this tab; no real AI calls or account changes. Sample sharing is local to this browser session.</p>
  </aside>;
}
