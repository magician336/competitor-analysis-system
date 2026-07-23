import type { AnalysisTimePreset } from "../types/api";

export const ANALYSIS_TIME_OPTIONS: Array<{ value: AnalysisTimePreset; label: string; days: number | null }> = [
  { value: "7d", label: "近一周", days: 7 },
  { value: "30d", label: "近一个月", days: 30 },
  { value: "180d", label: "近半年", days: 180 },
  { value: "all", label: "全部", days: null }
];

export function analysisTimeLabel(preset: AnalysisTimePreset | null | undefined): string {
  return ANALYSIS_TIME_OPTIONS.find((item) => item.value === preset)?.label || "历史范围";
}

export function isAnalysisTimePreset(value: unknown): value is AnalysisTimePreset {
  return ANALYSIS_TIME_OPTIONS.some((item) => item.value === value);
}

export function buildAnalysisWindow(
  preset: AnalysisTimePreset,
  now: Date = new Date()
): { start_time?: string; end_time?: string } {
  const option = ANALYSIS_TIME_OPTIONS.find((item) => item.value === preset);
  if (!option || option.days === null) return {};
  const end = new Date(now.getTime());
  const start = new Date(end.getTime() - option.days * 24 * 60 * 60 * 1000);
  return { start_time: start.toISOString(), end_time: end.toISOString() };
}

export function workflowCorrelationId(preset: AnalysisTimePreset): string {
  return `web-${preset}-${crypto.randomUUID()}`;
}

export function presetFromCorrelationId(value: string | null | undefined): AnalysisTimePreset | null {
  const matched = value?.match(/^web-(7d|30d|180d|all)-/);
  return matched && isAnalysisTimePreset(matched[1]) ? matched[1] : null;
}
