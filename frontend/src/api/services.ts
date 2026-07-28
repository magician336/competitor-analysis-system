import { http } from "./client";
import type {
  AskRequest,
  AskResponse,
  AskHistoryDetail,
  AskHistorySummary,
  AuthUser,
  AdminImportMetadata,
  AdminImportResponse,
  AdminDocumentListItem,
  AdminDocumentListQuery,
  AdminOverview,
  BriefingDetail,
  BriefingSummary,
  CardDetail,
  CardSummary,
  ComparisonResponse,
  Competitor,
  EvidenceDetail,
  EvidenceHistoryDetail,
  EvidenceHistorySummary,
  LoginRequest,
  Page,
  RAGQueryRequest,
  RAGResponse,
  RegisterRequest,
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

export const authApi = {
  register: async (payload: RegisterRequest) => (await http.post<AuthUser>("/api/auth/register", payload)).data,
  login: async (payload: LoginRequest) => (await http.post<AuthUser>("/api/auth/login", payload)).data,
  logout: async () => (await http.post("/api/auth/logout")).data,
  me: async () => (await http.get<AuthUser>("/api/auth/me")).data
};

export const adminApi = {
  overview: async () => (await http.get<AdminOverview>("/api/admin/overview")).data,
  documents: async (params: AdminDocumentListQuery) =>
    (await http.get<Page<AdminDocumentListItem>>("/api/admin/documents", {
      params: cleanQuery({ ...params })
    })).data,
  importDocuments: async (
    file: File,
    metadata: AdminImportMetadata,
    onProgress?: (percent: number) => void
  ) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(metadata).forEach(([key, raw]) => {
      if (raw === undefined || raw === null || raw === "") return;
      const value = Array.isArray(raw) ? JSON.stringify(raw) : String(raw);
      form.append(key, value);
    });
    return (await http.post<AdminImportResponse>("/api/admin/documents/import", form, {
      onUploadProgress: (event) => {
        if (!event.total || !onProgress) return;
        onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
      }
    })).data;
  }
};

export const askApi = {
  ask: async (payload: AskRequest) =>
    (await http.post<AskResponse>("/api/ask", payload, {
      timeout: 120_000
    })).data,
  history: async (params: Query = {}) =>
    (await http.get<Page<AskHistorySummary>>("/api/ask/history", { params: cleanQuery(params) })).data,
  detail: async (id: string) => (await http.get<AskHistoryDetail>(`/api/ask/history/${id}`)).data
};

export const ragApi = {
  query: async (payload: RAGQueryRequest) =>
    (await http.post<RAGResponse>("/api/rag/query", payload)).data,
  history: async (params: Query = {}) =>
    (await http.get<Page<EvidenceHistorySummary>>("/api/rag/history", { params: cleanQuery(params) })).data,
  detail: async (id: string) => (await http.get<EvidenceHistoryDetail>(`/api/rag/history/${id}`)).data
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
