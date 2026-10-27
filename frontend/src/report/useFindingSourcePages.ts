// F4/F7/F8: one revision-bound comparison, with serialized own-source reads.
import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { useAuthOwner } from "../auth/queryOwner";
import type { EvidenceLocation, EvidenceManifest, EvidencePage, EvidenceReference } from "./evidenceTypes";

export interface FindingSourceSide {
  role: EvidenceReference["role"];
  index: number;
  quote: string;
  anchor: string | null;
}

export function findingSourceReference(manifest: EvidenceManifest | undefined, flagId: number, side: FindingSourceSide) {
  const candidates = manifest?.references.filter(ref => ref.flag_id === flagId && ref.criterion_id === null
    && ref.role === side.role && ref.index === side.index) ?? [];
  const ref = candidates.length === 1 ? candidates[0] : undefined;
  return ref?.source_scope === "own_manuscript" && ref.quote === side.quote && ref.anchor === side.anchor ? ref : undefined;
}

const identity = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const sharedFailure = (error: unknown) => error instanceof ApiError && [401, 403, 404, 409, 410].includes(error.status);

export interface FindingSourcePage {
  role: FindingSourceSide["role"];
  page?: EvidencePage;
  failed?: boolean;
}

/** The query returns a complete generation; a shared denial cannot leave an earlier side current. */
export function useFindingSourcePages(run: number, flagId: number, revision: number | null,
  manifest: EvidenceManifest | undefined, sides: FindingSourceSide[], current: boolean, attempt = 0) {
  const owner = useAuthOwner();
  const references = sides.map(side => findingSourceReference(manifest, flagId, side));
  return useQuery({
    queryKey: ["finding-source-pages", owner.actorId, owner.lifetime, run, flagId, revision, manifest?.snapshot_id, attempt,
      sides.map((side, index) => [side.role, side.index, side.quote, side.anchor, references[index]?.reference_id])],
    enabled: current && owner.isCurrent() && manifest?.assessment_revision === revision && identity(manifest?.snapshot_id)
      && references.some(Boolean),
    gcTime: 0,
    retry: false,
    queryFn: async ({ signal }) => {
      if (!manifest || !owner.isCurrent()) throw new ApiError(409, "conflict", "The comparison changed.");
      const pages: FindingSourcePage[] = [];
      for (const [index, side] of sides.entries()) {
        const ref = references[index];
        if (!ref) { pages.push({ role: side.role }); continue; }
        try {
          if (!owner.isCurrent() || signal.aborted) throw new ApiError(409, "conflict", "The comparison changed.");
          const path = `/check-runs/${run}/evidence/${ref.reference_id}`;
          const location = await api.get<EvidenceLocation>(`${path}/location?snapshot_id=${manifest.snapshot_id}`, { signal });
          if (!owner.isCurrent() || signal.aborted || location.snapshot_id !== manifest.snapshot_id
            || location.placement?.reference_id !== ref.reference_id || typeof location.caption !== "string"
            || ![null, "pdf", "docx"].includes(location.source_format)
            || (location.source_identity !== null && !identity(location.source_identity))) {
            throw new ApiError(409, "conflict", "The comparison changed.");
          }
          let page: EvidencePage = { snapshot_id: location.snapshot_id, placement: location.placement,
            image_data_url: null, caption: location.caption };
          if (location.source_format === "pdf" && location.source_identity !== null) {
            page = await api.get<EvidencePage>(`${path}/page?snapshot_id=${manifest.snapshot_id}`, { signal });
            if (!owner.isCurrent() || signal.aborted || page.snapshot_id !== manifest.snapshot_id
              || page.placement?.reference_id !== ref.reference_id || typeof page.caption !== "string"
              || !(page.image_data_url === null || typeof page.image_data_url === "string"
                && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(page.image_data_url))) {
              throw new ApiError(409, "conflict", "The comparison changed.");
            }
          }
          pages.push({ role: side.role, page });
        } catch (error) {
          if (signal.aborted || !owner.isCurrent() || sharedFailure(error)) throw error;
          pages.push({ role: side.role, failed: true });
        }
      }
      if (!owner.isCurrent() || signal.aborted) throw new ApiError(409, "conflict", "The comparison changed.");
      return pages;
    },
  });
}
