export function SourceViewDetails({ id, open, format, comparisonMarkers }: {
  id: string; open: boolean; format: "pdf" | "docx"; comparisonMarkers: boolean;
}) {
  return <section id={id} hidden={!open} className="signal-source-details" aria-label="Source view details">
    {format === "docx" && <p>This view reconstructs extracted text, tables and available embedded images in source order. It does not reproduce Word pages. Merged cells, drawing placement, image cropping and rotation may differ. Showing an image does not mean its contents were assessed.</p>}
    {comparisonMarkers && <p>L markers identify optional comparison records. They do not represent a score or a confirmed problem.</p>}
    {format === "pdf" && <p>This view displays the original PDF. A recorded location can identify a page or a passage; the selected evidence explains how exact that location is.</p>}
  </section>;
}
