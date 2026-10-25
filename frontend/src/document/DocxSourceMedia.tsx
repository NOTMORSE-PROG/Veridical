// V-081 / screens 4h, 4k, 4w: original DOCX media, never a rendered Word page.
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { DocumentImageOut, DocumentTableOut } from "../api/types";
import { Button } from "../ui/Button";
import { Modal, ModalBackdrop } from "../components/Modal";
import { FigureReadingDetails } from "../report/FigureReadings";
import { NativeMathPieces } from "./NativeMathSource";

const UNAVAILABLE: Record<string, string> = {
  unsupported_drawing: "This drawing cannot be displayed in the reconstructed view.",
  external_image: "This image links outside the manuscript and was not downloaded.",
  invalid_image: "This embedded image could not be read safely.",
  image_limit: "This image exceeds the configured source-view limit.",
  source_not_recorded: "This saved extraction has no verified image source. The original file is unchanged.",
};

function SourceImage({ image, basePath, sourceQuery, loadImage = api.getBlob, isVisible, readingOnly = false }: {
  image: DocumentImageOut;
  basePath?: string;
  sourceQuery?: string;
  loadImage?: (path: string) => Promise<Blob>;
  isVisible: boolean;
  readingOnly?: boolean;
}) {
  const holder = useRef<HTMLElement>(null);
  const [inView, setInView] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [actualSize, setActualSize] = useState(false);
  const number = image.image_index + 1;
  const available = image.available && !!basePath;

  useEffect(() => {
    if (!available) return;
    if (typeof IntersectionObserver === "undefined") {
      if (isVisible) setInView(true);
      return;
    }
    // Both panels stay visible at desktop widths even when Review is selected.
    // Actual intersection, not the compact-layout tab, controls image loading.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setInView(true);
        observer.disconnect();
      }
    });
    if (holder.current) observer.observe(holder.current);
    return () => observer.disconnect();
  }, [isVisible, available]);

  useEffect(() => {
    if (!inView || !available) return;
    let disposed = false;
    let objectUrl: string | null = null;
    setFailed(false);
    setUrl(null);
    loadImage(`${basePath}/${image.image_index}${sourceQuery ? `?${sourceQuery}` : ""}`).then((blob) => {
      if (disposed) return;
      if (blob.type !== "image/png" && blob.type !== "image/jpeg") {
        setFailed(true);
        return;
      }
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => {
      if (!disposed) setFailed(true);
    });
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [inView, available, basePath, sourceQuery, loadImage, image.image_index, attempt]);

  const alt = image.source_alt_text || `Original embedded image ${number}`;
  return (
    <figure ref={holder} className="signal-docx-source-image">
      <figcaption>
        <strong>Source image {number}</strong>
        <span>Image contents are not assessed by this source view.</span>
      </figcaption>
      {!available ? <p>{UNAVAILABLE[image.unavailable_reason ?? "source_not_recorded"] ?? "This source image is unavailable."}</p>
        : failed ? <div role="status"><p>This image could not be loaded. The surrounding {readingOnly ? "format" : "manuscript"} remains available.</p>
          <Button variant="secondary" onClick={() => setAttempt((value) => value + 1)}>Retry image {number}</Button></div>
          : url ? <>
            <img src={url} alt={alt} width={image.width_px ?? undefined} height={image.height_px ?? undefined}
              onError={() => setFailed(true)} />
            <Button variant="secondary" onClick={() => { setActualSize(false); setExpanded(true); }}>Expand image {number}</Button>
          </> : <p role="status">{inView ? "Loading source image." : "Image will load when visible."}</p>}
      {url && !failed && !readingOnly && <FigureReadingDetails reading={image.figure_reading} />}
      {expanded && url && !failed && <ModalBackdrop>
        <Modal title={`Source image ${number}`} size="lg" onClose={() => setExpanded(false)}>
          <p>Original embedded image at extracted paragraph {image.paragraph + 1}. Word cropping and rotation are not reproduced. Display does not verify the contents.</p>
          <div className="signal-docx-image-controls" role="group" aria-label="Image size">
            <Button variant="secondary" aria-pressed={!actualSize} onClick={() => setActualSize(false)}>Fit width</Button>
            <Button variant="secondary" aria-pressed={actualSize} onClick={() => setActualSize(true)}>Actual size</Button>
          </div>
          <div className="signal-docx-image-expanded" tabIndex={0} role="region" aria-label={`Source image ${number}, scrollable`}>
            <img src={url} alt={alt} width={image.width_px ?? undefined} height={image.height_px ?? undefined}
              className={actualSize ? "is-actual-size" : ""} />
          </div>
          {!readingOnly && <FigureReadingDetails reading={image.figure_reading} />}
        </Modal>
      </ModalBackdrop>}
    </figure>
  );
}

export function DocxSourceMedia({ tables, images, imageBasePath, sourceQuery, loadImage, isVisible, readingOnly = false }: {
  tables: DocumentTableOut[];
  images: DocumentImageOut[];
  imageBasePath?: string;
  sourceQuery?: string;
  loadImage?: (path: string) => Promise<Blob>;
  isVisible: boolean;
  readingOnly?: boolean;
}) {
  return <>
    {tables.map((table, index) => {
      const nativeByCoordinate = new Map<string, NonNullable<DocumentTableOut["native_cells"]>[number]>();
      for (const cell of table.native_cells ?? []) {
        for (const [row, column] of cell.display_coordinates) {
          const coordinate = `${row}:${column}`;
          if (!nativeByCoordinate.has(coordinate)) nativeByCoordinate.set(coordinate, cell);
        }
      }
      return (
      <div key={index} className="signal-docx-source-table" role="region" tabIndex={0}
        aria-label={`Table at extracted paragraph ${table.paragraph + 1}, scrollable`}>
        <table>
          <caption>Source table · Extracted paragraph {table.paragraph + 1}</caption>
          <tbody>{table.rows.map((row, rowIndex) => <tr key={rowIndex}>
            {row.map((cell, cellIndex) => {
              const native = nativeByCoordinate.get(`${rowIndex}:${cellIndex}`);
              return <td key={cellIndex}>{native ? <NativeMathPieces pieces={native.pieces} /> : cell}</td>;
            })}
          </tr>)}</tbody>
        </table>
        {table.native_cells?.filter((cell) => cell.display_coordinates.length === 0).map((cell, cellIndex) =>
          <div key={cellIndex} className="signal-document-reconstruction-note">
            <p>Native Word equation in a nested table cell. Its cell location in this reconstruction is unavailable.</p>
            <p><NativeMathPieces pieces={cell.pieces} /></p>
          </div>)}
      </div>
      );
    })}
    {images.map((image) => <SourceImage key={`${imageBasePath}/${image.image_index}?${sourceQuery ?? ""}`}
      image={image} basePath={imageBasePath} sourceQuery={sourceQuery} loadImage={loadImage} isVisible={isVisible} readingOnly={readingOnly} />)}
  </>;
}
