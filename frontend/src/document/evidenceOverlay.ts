import type { EvidencePlacement } from "../report/evidenceTypes";

/** PDF coordinates retain their actual dimensions; larger hit areas are separate. */
export function evidenceOverlayRects(placement: EvidencePlacement | undefined, page: number,
  convert: (x: number, y: number) => number[]) {
  if (placement?.state !== "text_layer_match" || placement.page !== page) return [];
  return placement.boxes.flatMap(box => {
    if (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= box[0] || box[3] <= box[1]) return [];
    const [x0, y0] = convert(box[0], box[1]);
    const [x1, y1] = convert(box[2], box[3]);
    if (![x0, y0, x1, y1].every(Number.isFinite)) return [];
    return [{ left: Math.min(x0, x1), top: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) }];
  });
}
