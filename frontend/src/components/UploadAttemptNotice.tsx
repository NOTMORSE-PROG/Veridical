// F1/F2, screens 4c/4e/4m: inspection never reconciles an unknown request.
import { useEffect, useRef } from "react";
export function UploadAttemptNotice({ kind, filenames, selectedFilename, familyId, confirming, onConfirm, onCancel }: {
  kind: "manuscript" | "format"; filenames: string[]; selectedFilename?: string; familyId?: string;
  confirming: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const confirmationHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (confirming) confirmationHeading.current?.focus(); }, [confirming]);
  if (filenames.length === 0) return null;
  return <section className="signal-group-flow rounded-md border border-border bg-status-caution-bg px-3 py-3 text-sm text-status-caution-text" aria-label="Unconfirmed upload results">
    <h3 className="font-bold">One or more upload results remain unconfirmed</h3>
    <p>VERIDICAL could not confirm whether these attempts saved a record: {filenames.join(", ")}. A later confirmed result applies only to that later attempt.</p>
    <p>Closing this form or continuing to another screen leaves earlier results unconfirmed. The selected file and these local attempt notices will not be retained after you leave.</p>
    {kind === "manuscript" ? <>
      <a className="font-bold underline" href="/dashboard?queue=not_checked&sort=newest&page=1" target="_blank" rel="noopener noreferrer">Review uploads (opens in new tab)</a>
      <p>Check Not checked and Needs you in Review Desk. A record may not appear yet. A missing record does not confirm that an attempt created nothing.</p>
    </> : <>
      <a className="font-bold underline" href="/rubric" target="_blank" rel="noopener noreferrer">Review format records (opens in new tab)</a>
      <p>This opens all your format records. Check the intended format and its version history. A different selected format does not confirm this upload's result. A missing draft does not confirm that an attempt created nothing.</p>
      {familyId && <details><summary>Original format context</summary><p>Submitted format family: {familyId}</p></details>}
    </>}
    {confirming && <div className="signal-group-flow">
      <h4 ref={confirmationHeading} tabIndex={-1} className="font-bold">Confirm a new upload request</h4>
      <p>New request: {selectedFilename ?? "no file selected"}. {kind === "format"
        ? familyId ? `New version of format family ${familyId}.` : "New format family."
        : "New manuscript copy."}</p>
      <p>{kind === "manuscript" ? "Another copy may be saved." : "Another draft or version may be saved, and format parsing may use AI capacity."} This starts one new request. Earlier results remain unconfirmed.</p>
      <div className="flex gap-3">
        <button type="button" onClick={onCancel} className="font-bold underline">Keep reviewing</button>
        <button type="button" onClick={onConfirm} className="font-bold underline">{kind === "manuscript" ? "Upload a new copy" : "Upload a new draft"}</button>
      </div>
    </div>}
  </section>;
}

export function UploadSessionNotice({ status, onCheck, onClose }: {
  status: "checking" | "unavailable" | "lost"; onCheck: () => void; onClose: () => void;
}) {
  const notice = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (status !== "checking" && document.activeElement === document.body) notice.current?.focus();
  }, [status]);
  return <div className="signal-group-flow">
    <p ref={notice} tabIndex={-1} role={status === "checking" ? "status" : "alert"}>{status === "checking"
      ? "Checking your sign-in status."
      : status === "lost" ? "Your sign-in context changed. Close this form and start a new upload."
        : "Could not confirm your sign-in status. Check your session before continuing."}</p>
    {status === "unavailable" && <button type="button" className="font-bold underline" onClick={onCheck}>Check sign-in status</button>}
    <button type="button" className="font-bold underline" onClick={onClose}>Close form</button>
  </div>;
}
