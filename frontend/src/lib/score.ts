/** Shared score -> color-band class, used by both the Jobs and Tracking
 * tables so a given score always reads the same color in either place. */
export function scoreClass(score: number | undefined) {
  if (score === undefined) return "";
  if (score >= 70) return "score-high";
  if (score >= 40) return "score-mid";
  return "score-low";
}
