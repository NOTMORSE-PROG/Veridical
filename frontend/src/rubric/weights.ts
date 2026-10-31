// BUG-580: criterion weights are relative (scoring divides by their total),
// so the instructor sees them as plain numbers, never as a computed split
// such as 11.111. Same rule as the backend's relative_weights().
import { RUBRIC_WEIGHT_MAX_DECIMALS, RUBRIC_WEIGHT_STORED } from "../config/ui";

function plain(weight: number): boolean {
  return Math.abs(weight - Number(weight.toFixed(RUBRIC_WEIGHT_MAX_DECIMALS))) < 1e-9;
}

/** Weights as stated when they are plain numbers; otherwise rescaled so the
 * lightest is 1, which keeps their ratios. Weights too uneven for the
 * server to store that way fall back to a split of 100. */
export function readableWeights(weights: readonly number[]): number[] {
  if (weights.every(plain)) return [...weights];
  const positive = weights.filter((weight) => weight > 0);
  if (!positive.length) return [...weights];
  const lightest = Math.min(...positive);
  const readable = weights.map((weight) => weight > 0 ? Number((weight / lightest).toFixed(RUBRIC_WEIGHT_MAX_DECIMALS)) : weight);
  if (Math.max(...readable) <= RUBRIC_WEIGHT_STORED.max) return readable;
  const total = positive.reduce((sum, weight) => sum + weight, 0);
  return weights.map((weight) => weight > 0 ? Number((weight / total * 100).toFixed(RUBRIC_WEIGHT_STORED.decimals)) : weight);
}

/** True when every criterion carries the same weight. */
export function equalWeights(weights: readonly number[]): boolean {
  return weights.length > 1 && weights.every((weight) => weight === weights[0]);
}
