import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "../api/client";
import { useAuthOwner } from "../auth/queryOwner";
import type { EvidenceDocument, EvidenceLocation, EvidenceManifest } from "./evidenceTypes";

const isIdentity = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

export function useEvidenceManifest(run: number, revision: number | null | undefined, enabled = true) {
  const owner = useAuthOwner();
  return useQuery({
    queryKey: ["report", run, "evidence", owner.actorId, owner.lifetime, revision],
    enabled: enabled && owner.isCurrent() && Number.isInteger(run) && run > 0 && Number.isInteger(revision),
    gcTime: 0,
    queryFn: async ({ signal }) => {
      const out = await api.get<EvidenceManifest>(`/check-runs/${run}/evidence`, { signal });
      if (!owner.isCurrent() || out.assessment_revision !== revision || !isIdentity(out.snapshot_id)
        || !Array.isArray(out.references) || out.references.some(ref => !isIdentity(ref.reference_id))) {
        throw new ApiError(409, "conflict", "This assessment changed. Reload its recorded evidence.");
      }
      return out;
    },
    retry: false,
  });
}

export function useEvidenceLocation(run: number, snapshot: string | undefined, referenceId: string | undefined, enabled: boolean, attempt = 0) {
  const owner = useAuthOwner();
  return useQuery({
    queryKey: ["report", run, "evidence-location", owner.actorId, owner.lifetime, snapshot, referenceId, attempt],
    enabled: enabled && owner.isCurrent() && isIdentity(snapshot) && isIdentity(referenceId),
    gcTime: 0,
    queryFn: async ({ signal }) => {
      const out = await api.get<EvidenceLocation>(`/check-runs/${run}/evidence/${referenceId}/location?snapshot_id=${snapshot}`, { signal });
      if (!owner.isCurrent() || out.snapshot_id !== snapshot || out.placement.reference_id !== referenceId
        || (out.source_identity !== null && !isIdentity(out.source_identity))) {
        throw new ApiError(409, "conflict", "The selected evidence location changed. Reload its source.");
      }
      return out;
    },
    retry: false,
  });
}

export function useEvidenceDocument(run: number, identity: string | null | undefined, enabled: boolean) {
  const owner = useAuthOwner();
  return useQuery({
    queryKey: ["evidence-reader", run, owner.actorId, owner.lifetime, identity],
    enabled: enabled && owner.isCurrent() && isIdentity(identity),
    gcTime: 0,
    queryFn: async ({ signal }) => {
      const out = await api.get<EvidenceDocument>(`/check-runs/${run}/evidence/source-document?source_identity=${identity}`, { signal });
      if (!owner.isCurrent() || out.source_identity !== identity) {
        throw new ApiError(409, "conflict", "This source copy changed. Reload its evidence location.");
      }
      return out;
    },
    retry: false,
  });
}
