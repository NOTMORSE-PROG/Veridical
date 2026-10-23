export function SourceViewDetails({ id, open, format, comparisonMarkers }: {
  id: string; open: boolean; format: "pdf" | "docx"; comparisonMarkers: boolean;
}) {
  return <section id={id} hidden={!open} className="signal-source-details" aria-label="Source view details">
    {format === "docx" && <p>This view reconstructs extracted text, tables and available embedded images in source order. It does not reproduce Word pages. Merged cells, drawing placement, image cropping and rotation may differ. Showing an image does not mean its contents were assessed.</p>}
    {format === "docx" && <p>Native Word equations are not displayed in this reconstruction. Check the original DOCX for their formulas and layout. The native inventory covers the main document body; it does not establish coverage of headers, footnotes or other package parts.</p>}
    {comparisonMarkers && <p>L markers identify optional comparison records. They do not represent a score or a confirmed problem.</p>}
    {format === "pdf" && <p>This view displays the original PDF. A recorded location can identify a page or a passage; the selected evidence explains how exact that location is.</p>}
  </section>;
}
