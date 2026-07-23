import type { CapabilityScore, CapabilitySnapshot, Dimension } from "../types/api";
import { DIMENSIONS } from "../types/api";

const order = new Map<Dimension, number>(DIMENSIONS.map((item, index) => [item.key, index]));

export function scoredCapabilities(snapshot: CapabilitySnapshot): CapabilityScore[] {
  return snapshot.details
    .filter((item) => item.status === "scored")
    .sort((left, right) => (order.get(left.dimension) || 0) - (order.get(right.dimension) || 0));
}

export function missingCapabilities(snapshot: CapabilitySnapshot): CapabilityScore[] {
  return snapshot.details
    .filter((item) => item.status === "insufficient_evidence")
    .sort((left, right) => (order.get(left.dimension) || 0) - (order.get(right.dimension) || 0));
}

export function capabilityLabel(dimension: Dimension): { code: string; name: string } {
  return DIMENSIONS.find((item) => item.key === dimension) || { code: dimension, name: dimension };
}

export function capabilitySummary(snapshot: CapabilitySnapshot) {
  const scored = scoredCapabilities(snapshot);
  const evidenceTotal = scored.reduce((sum, item) => sum + item.evidence_count, 0);
  const averageConfidence = scored.length
    ? scored.reduce((sum, item) => sum + item.confidence, 0) / scored.length
    : 0;
  return {
    scoredCount: scored.length,
    totalCount: snapshot.details.length,
    evidenceTotal,
    averageConfidence,
    coverageRatio: snapshot.coverage_ratio
  };
}
