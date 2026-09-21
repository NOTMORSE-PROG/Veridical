import type { FlagRegionOut, FlagSummaryOut } from "../api/types";

export interface ReviewFinding {
  key: string;
  number: number;
  flags: FlagSummaryOut[];
  representative: FlagSummaryOut;
  severity: FlagSummaryOut["severity"];
  isResolved: boolean;
  isSourceConfirmed: boolean;
}

const SEVERITY_RANK: Record<FlagSummaryOut["severity"], number> = {
  low: 0,
  med: 1,
  high: 2,
};

function reuseBaseKind(kind: string | null): string | null {
  if (!kind) return null;
  const match = kind.match(/^(reuse_(?:exact_duplicate|high_similarity))(?:_(?:chapter|passage))?$/);
  return match?.[1] ?? null;
}

function findingIdentity(flag: FlagSummaryOut): string {
  if (flag.check_kind !== "originality_reuse" || flag.matched_ref == null) {
    return `flag:${flag.id}`;
  }
  const baseKind = reuseBaseKind(flag.problem_kind);
  return baseKind
    ? JSON.stringify([flag.check_kind, baseKind, flag.matched_ref])
    : `flag:${flag.id}`;
}

function representativeRank(flag: FlagSummaryOut): number {
  if (flag.is_passage_level) return 3;
  if (flag.problem_kind?.endsWith("_chapter")) return 2;
  return 1;
}

function chooseRepresentative(flags: FlagSummaryOut[]): FlagSummaryOut {
  return [...flags].sort((left, right) => {
    const openDifference = Number(left.overridden) - Number(right.overridden);
    if (openDifference !== 0) return openDifference;
    const specificityDifference = representativeRank(right) - representativeRank(left);
    if (specificityDifference !== 0) return specificityDifference;
    return left.id - right.id;
  })[0];
}

/**
 * Builds the canonical review list once from the complete API response.
 * Display numbers never depend on a later filter or resolution state.
 *
 * F7 emits whole-document, chapter, and passage records for one detected
 * relationship. They collapse only when persisted fields establish the same
 * archived source and base outcome. Unknown identities stay separate.
 */
export function buildReviewFindings(flags: FlagSummaryOut[]): ReviewFinding[] {
  const order: string[] = [];
  const grouped = new Map<string, FlagSummaryOut[]>();

  for (const flag of flags) {
    const key = findingIdentity(flag);
    const members = grouped.get(key);
    if (members) members.push(flag);
    else {
      grouped.set(key, [flag]);
      order.push(key);
    }
  }

  return order.map((key, index) => {
    const members = grouped.get(key)!;
    const representative = chooseRepresentative(members);
    const severity = members.reduce<FlagSummaryOut["severity"]>(
      (highest, flag) => SEVERITY_RANK[flag.severity] > SEVERITY_RANK[highest] ? flag.severity : highest,
      members[0].severity,
    );
    return {
      key,
      number: index + 1,
      flags: members,
      representative,
      severity,
      isResolved: members.every((flag) => flag.overridden),
      isSourceConfirmed: members.every((flag) => flag.confirmed_citation_source),
    };
  });
}

export function findingForFlag(findings: ReviewFinding[], flagId: number | null): ReviewFinding | null {
  if (flagId === null) return null;
  return findings.find((finding) => finding.flags.some((flag) => flag.id === flagId)) ?? null;
}

export function findingNumberByFlagId(findings: ReviewFinding[]): Map<number, number> {
  return new Map(findings.flatMap((finding) => finding.flags.map((flag) => [flag.id, finding.number] as const)));
}

export function representativeRegions(
  findings: ReviewFinding[],
  regions: FlagRegionOut[],
): FlagRegionOut[] {
  const byFlag = new Map(regions.map((region) => [region.flag_id, region]));
  return findings.flatMap((finding) => {
    const preferred = byFlag.get(finding.representative.id);
    if (preferred) return [preferred];
    const fallback = finding.flags.map((flag) => byFlag.get(flag.id)).find(Boolean);
    return fallback ? [fallback] : [];
  });
}

