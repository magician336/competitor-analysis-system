export type Dimension =
  | "code_intelligence"
  | "agent_context"
  | "ide_ecosystem"
  | "model_extensibility"
  | "performance_cost"
  | "security_compliance"
  | "education_fit";

export type WorkflowStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "success"
  | "partial_failure"
  | "failed"
  | "timed_out";

export type BranchStatus = "pending" | "running" | "success" | "failed" | "cancelled" | "skipped";
export type SpecialistBranch = "price" | "product" | "risk";
export type AgentMode = "rules" | "hybrid" | "llm";
export type AnalysisTimePreset = "7d" | "30d" | "180d" | "all";
export type AnalysisWizardStep = "mode" | "scope" | "task" | "review";

export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  request_id: string;
  instance?: string | null;
  errors: Array<Record<string, unknown>>;
}

export interface Competitor {
  id: string;
  name: string;
  aliases: string[];
  sources: Record<string, unknown>;
  enabled: boolean;
  config_version: number;
  created_at: string;
  updated_at: string;
}

export interface CardSummary {
  card_id: string;
  competitor: string;
  agent_kind: string;
  event_type: string;
  event_title: string;
  summary: string;
  alert_level: string;
  confidence_score: number;
  priority_score: number;
  review_required: boolean;
  evidence_count: number;
  publish_time: string | null;
  created_at: string;
}

export interface EvidenceReference {
  citation_id?: string | null;
  chunk_id: string;
  document_id: string;
  version_id: string;
  title: string;
  url: string;
  competitor: string;
  source_type: string;
  evidence_level: string;
  event_type?: string | null;
  dimension_tags: Dimension[];
  product_version?: string | null;
  publish_time?: string | null;
  quote?: string | null;
  final_score?: number | null;
  content?: string;
}

export interface AskRequest {
  question: string;
  analysis_target?: string;
  start_time?: string;
  end_time?: string;
  top_k: number;
}

export interface AskResponse {
  ask_id: string;
  query_id: string;
  answer: string;
  answer_mode: "hybrid" | "rules_fallback";
  references: EvidenceReference[];
  conflicts: Array<Record<string, unknown>>;
  generated_at: string;
}

export interface AskTurn {
  turn_id: string;
  question: string;
  target: string;
  time_preset: AnalysisTimePreset;
  status: "pending" | "success" | "error";
  response?: AskResponse;
  error?: string;
  created_at: string;
}

export interface IntelligenceCard extends CardSummary {
  schema_version: string;
  evidence: EvidenceReference[];
  findings?: Array<Record<string, unknown>>;
  capability_impacts?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface CardDetail {
  card: IntelligenceCard;
  evidence_links: EvidenceReference[];
}

export interface EvidenceDetail {
  evidence: EvidenceReference;
  card_ids: string[];
}

export interface SnapshotSummary {
  snapshot_id: string;
  competitor: string;
  snapshot_date: string;
  product_version: string;
  scoring_version: string;
  total_score: number;
  overall_confidence: number;
  coverage_ratio: number;
  previous_snapshot_id?: string | null;
  source_kind: string;
  created_at: string;
}

export interface CapabilityScore {
  dimension: Dimension;
  score: number;
  status: "scored" | "insufficient_evidence";
  confidence: number;
  evidence_count: number;
  rationale: string;
  delta?: number | null;
}

export interface CapabilitySnapshot {
  snapshot_id: string;
  competitor: string;
  snapshot_date: string;
  product_version: string;
  scoring_version: string;
  window_start?: string | null;
  window_end?: string | null;
  scores: Partial<Record<Dimension, number>>;
  confidence: Partial<Record<Dimension, number>>;
  evidence_count: Partial<Record<Dimension, number>>;
  details: CapabilityScore[];
  total_score: number;
  overall_confidence: number;
  coverage_ratio: number;
  previous_snapshot_id?: string | null;
  deltas: Partial<Record<Dimension, number>>;
}

export interface SnapshotDetail {
  snapshot: CapabilitySnapshot;
  source_kind: string;
  provenance: Record<string, unknown>;
}

export interface MatrixCell {
  product: string;
  dimension: Dimension;
  status: "scored" | "insufficient_evidence";
  score: number | null;
  confidence: number;
  evidence_count: number;
  gap_to_baseline: number | null;
  delta_from_previous: number | null;
}

export interface MatrixRow {
  dimension: Dimension;
  cells: MatrixCell[];
  valid_product_count: number;
  mean_score: number | null;
  score_spread: number | null;
}

export interface ProductComparison {
  product: string;
  snapshot_id: string;
  coverage_ratio: number;
  weighted_total_score: number | null;
  weighted_confidence: number;
  rank_eligible: boolean;
  rank: number | null;
  gap_to_baseline_total: number | null;
  comparable_trend_coverage: number;
  weighted_delta: number | null;
  trend_eligible: boolean;
  dimension_deltas: Partial<Record<Dimension, number>>;
  ranking_reason: string;
  trend_reason: string;
}

export interface GapTrend {
  product: string;
  dimension: Dimension;
  previous_gap_to_baseline: number;
  current_gap_to_baseline: number;
  absolute_gap_change: number;
  status: string;
  basis: string;
}

export interface TableStake {
  dimension: Dimension;
  status: "table_stakes" | "not_table_stakes" | "insufficient_data";
  score_threshold: number;
  valid_product_count: number;
  qualifying_product_count: number;
  qualifying_products: string[];
  basis: string;
}

export interface ComparisonMatrix {
  baseline_product: string;
  scoring_version: string;
  comparison_date: string;
  current_snapshot_ids: Record<string, string>;
  previous_snapshot_ids: Record<string, string>;
  rows: MatrixRow[];
  products: ProductComparison[];
  fastest_growth?: Record<string, unknown> | null;
  most_competitive_dimension?: Record<string, unknown> | null;
  gap_trends: GapTrend[];
  table_stakes: TableStake[];
}

export interface ComparisonResponse {
  comparison_id: string;
  request_fingerprint: string;
  official_ranking_ready: boolean;
  matrix: ComparisonMatrix;
  provenance: Record<string, unknown>;
  created_at: string;
}

export interface WorkflowSummary {
  workflow_id: string;
  competitor: string;
  analysis_mode: AgentMode;
  status: WorkflowStatus;
  progress: number;
  attempt_count: number;
  max_attempts: number;
  partial_failure: boolean;
  correlation_id?: string | null;
  submitted_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  last_error?: Record<string, unknown> | null;
}

export interface WorkflowBranch {
  branch: SpecialistBranch;
  status: BranchStatus;
  progress: number;
  last_attempt: number;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  rag_query_id?: string | null;
  card_ids: string[];
  trace_id?: string | null;
  error?: Record<string, unknown> | null;
}

export interface WorkflowDetail extends WorkflowSummary {
  request_fingerprint?: string | null;
  cancel_requested: boolean;
  started_at?: string | null;
  deadline_at?: string | null;
  branches: WorkflowBranch[];
  card_ids: string[];
  snapshot_id?: string | null;
  briefing_id?: string | null;
  result?: Record<string, unknown> | null;
}

export interface WorkflowSubmitRequest {
  competitor: string;
  analysis_mode: AgentMode;
  question?: string;
  branches: SpecialistBranch[];
  start_time?: string;
  end_time?: string;
  top_k: number;
  max_cards: number;
  include_snapshot: boolean;
  include_briefing: boolean;
  current_only: boolean;
  correlation_id?: string;
}

export interface WorkflowSubmission {
  workflow_id: string;
  status: WorkflowStatus;
  deduplicated: boolean;
  status_url: string;
  submitted_at?: string | null;
}

export interface BriefingSummary {
  briefing_id: string;
  competitor: string;
  snapshot_id?: string | null;
  workflow_id?: string | null;
  created_at: string;
}

export interface BriefingDetail extends BriefingSummary {
  markdown: string;
}

export interface SystemHealth { status: string }
export interface ReadyStatus {
  status: string;
  index: string;
  indexed_chunks: number;
  embedding_compatible: boolean;
  backend: { status: string; number_of_nodes: number };
}

export const DIMENSIONS: Array<{ key: Dimension; code: string; name: string }> = [
  { key: "code_intelligence", code: "D1", name: "代码智能" },
  { key: "agent_context", code: "D2", name: "Agent 与上下文" },
  { key: "ide_ecosystem", code: "D3", name: "IDE 生态" },
  { key: "model_extensibility", code: "D4", name: "模型扩展" },
  { key: "performance_cost", code: "D5", name: "性能与成本" },
  { key: "security_compliance", code: "D6", name: "安全合规" },
  { key: "education_fit", code: "D7", name: "教育适配" }
];
