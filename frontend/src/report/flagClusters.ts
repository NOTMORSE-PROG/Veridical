import type { FlagSummaryOut } from "../api/types";

export interface FlagFindingCluster {
  key: string;
  flags: FlagSummaryOut[];
}

function findingIdentity(flag: FlagSummaryOut): string {
  if (!flag.problem_kind) return `flag:${flag.id}`;

  if (flag.check_kind === "originality_reuse") {
    return flag.matched_ref == null
      ? `flag:${flag.id}`
      : JSON.stringify([flag.check_kind, flag.problem_kind, flag.matched_ref]);
  }

  return JSON.stringify([
    flag.check_kind,
    flag.problem_kind,
    flag.criterion_text,
    flag.evidence_excerpt.trim(),
  ]);
}

/** BUG-141: collapse rows only when persisted fields establish one finding.
 * Order follows the API; the minimum member id stays stable when unresolved
 * and resolved rows reorder around each other. Unknown identities remain
 * singletons instead of being guessed from similar prose. */
export function clusterFlagFindings(flags: FlagSummaryOut[]): FlagFindingCluster[] {
  const order: string[] = [];
  const byIdentity = new Map<string, FlagSummaryOut[]>();

  for (const flag of flags) {
    const identity = findingIdentity(flag);
    const existing = byIdentity.get(identity);
    if (existing) {
      existing.push(flag);
    } else {
      byIdentity.set(identity, [flag]);
      order.push(identity);
    }
  }

  return order.map((identity) => {
    const members = byIdentity.get(identity)!;
    return {
      key: String(Math.min(...members.map((flag) => flag.id))),
      flags: members,
    };
  });
}
