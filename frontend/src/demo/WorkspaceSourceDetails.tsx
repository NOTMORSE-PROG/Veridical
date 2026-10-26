import { companionSource, WORKSPACE_SAMPLE, isWorkspaceSample } from "./workspaceFixture";
import "./workspaceSample.css";

export function WorkspaceSourceDetails({ libraryId }: { libraryId?: number }) {
  if (!isWorkspaceSample()) return null;
  const source = libraryId === companionSource.id ? companionSource : WORKSPACE_SAMPLE;
  return <details className="workspace-source-details">
    <summary>Source and review details</summary>
    <p>{source.attribution}</p>
    {!libraryId && <p>The observations come from reading the complete manuscript, its code, figures and screenshots. The completed AI assessment was prepared in this session. The app's automated grading service was not run. The teacher can review the grades before making the final decision.</p>}
    <p><a href={source.pdfAsset} download={source.filename}>Download original PDF</a> · <a href={source.source} target="_blank" rel="noreferrer">Published source and license</a></p>
    {!libraryId && <><p>This temporary workspace saves practice changes in this tab. Shared reports open only in this browser.</p><p><button type="button" onClick={() => { if (window.confirm("Reset this tab's practice judgments and decisions?")) { sessionStorage.removeItem(WORKSPACE_SAMPLE.storageKey); window.location.assign("/demo/dashboard"); } }}>Reset workspace</button> · <a href="/signin">Open account sign-in</a></p></>}
  </details>;
}
