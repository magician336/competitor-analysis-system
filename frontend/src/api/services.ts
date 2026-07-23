import { http } from "./client";
import type {
  AskRequest,
  AskResponse,
  BriefingDetail,
  BriefingSummary,
  CardDetail,
  CardSummary,
  ComparisonResponse,
  Competitor,
  EvidenceDetail,
  Page,
  ReadyStatus,
  SnapshotDetail,
  SnapshotSummary,
  SystemHealth,
  WorkflowDetail,
  WorkflowSubmission,
  WorkflowSubmitRequest,
  WorkflowSummary
} from "../types/api";

export type Query = Record<string, string | number | boolean | undefined | null>;

export function cleanQuery(params: Query): Query {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== null && value !== "")
  );
}

export const systemApi = {
  health: async () => (await http.get<SystemHealth>("/health")).data,
  ready: async () => (await http.get<ReadyStatus>("/ready")).data
};

export const askApi = {
  ask: async (payload: AskRequest) =>
    (await http.post<AskResponse>("/api/ask", payload)).data
};

export const competitorApi = {
  list: async (enabledOnly = false) =>
    (await http.get<Competitor[]>("/api/competitors", { params: { enabled_only: enabledOnly } })).data
};

export const cardApi = {
  list: async (params: Query) => (await http.get<Page<CardSummary>>("/api/cards", { params: cleanQuery(params) })).data,
  detail: async (id: string) => (await http.get<CardDetail>(`/api/cards/${id}`)).data,
  evidence: async (chunkId: string) =>
    (await http.get<EvidenceDetail>(`/api/evidence/${chunkId}`)).data
};

export const snapshotApi = {
  list: async (params: Query) => (await http.get<Page<SnapshotSummary>>("/api/snapshots", { params: cleanQuery(params) })).data,
  detail: async (id: string) => (await http.get<SnapshotDetail>(`/api/snapshots/${id}`)).data
};

export const comparisonApi = {
  latest: async () => (await http.get<ComparisonResponse>("/api/comparisons/latest")).data,
  get: async (id: string) => (await http.get<ComparisonResponse>(`/api/comparisons/${id}`)).data
};

export const workflowApi = {
  list: async (params: Query) => (await http.get<Page<WorkflowSummary>>("/api/workflows", { params: cleanQuery(params) })).data,
  submit: async (payload: WorkflowSubmitRequest) =>
    (await http.post<WorkflowSubmission>("/api/workflows", payload)).data,
  detail: async (id: string) => (await http.get<WorkflowDetail>(`/api/workflows/${id}`)).data,
  cancel: async (id: string) => (await http.post(`/api/workflows/${id}/cancel`)).data,
  retry: async (id: string) => (await http.post(`/api/workflows/${id}/retry`)).data
};

export const briefingApi = {
  list: async (params: Query) => (await http.get<Page<BriefingSummary>>("/api/briefings", { params: cleanQuery(params) })).data,
  detail: async (id: string) => (await http.get<BriefingDetail>(`/api/briefings/${id}`)).data,
  download: async (id: string) => {
    const response = await http.get<Blob>(`/api/briefings/${id}/content`, { responseType: "blob" });
    const disposition = String(response.headers["content-disposition"] || "");
    const matched = disposition.match(/filename="?([^";]+)"?/i);
    const name = matched?.[1] || `${id}.md`;
    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    URL.revokeObjectURL(url);
  }
};
