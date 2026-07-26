import { describe, expect, it } from "vitest";
import MockAdapter from "axios-mock-adapter";
import { adminApi, askApi, authApi, cleanQuery, ragApi } from "../api/services";
import { http } from "../api/client";

describe("query parameter normalization", () => {
  it("drops blank filters and preserves false and zero", () => {
    expect(cleanQuery({ competitor: "", agent_kind: undefined, review_required: false, min_priority: 0, page: 1 })).toEqual({
      review_required: false,
      min_priority: 0,
      page: 1
    });
  });
});

describe("ask service", () => {
  it("posts the public one-shot ask contract", async () => {
    const mock = new MockAdapter(http);
    mock.onPost("/api/ask").reply((config) => {
      expect(JSON.parse(config.data).analysis_target).toBe("Cursor");
      return [200, { ask_id: "a1", query_id: "q1", answer: "answer [1]", answer_mode: "hybrid", references: [], conflicts: [], generated_at: "2026-07-22T00:00:00Z" }];
    });
    const response = await askApi.ask({ question: "question", analysis_target: "Cursor", top_k: 8 });
    expect(response.answer_mode).toBe("hybrid");
    mock.restore();
  });

  it("loads the current user's paginated ask history and full detail", async () => {
    const mock = new MockAdapter(http);
    mock.onGet("/api/ask/history").reply((config) => {
      expect(config.params).toEqual({ page: 2, page_size: 10 });
      return [200, { items: [{ ask_id: "a1", question: "question", generated_at: "2026-07-26T00:00:00Z" }], page: 2, page_size: 10, total: 11, total_pages: 2 }];
    });
    mock.onGet("/api/ask/history/a1").reply(200, { ask_id: "a1", request: { question: "question", top_k: 8 }, response: { ask_id: "a1", query_id: "q1", answer: "answer", answer_mode: "hybrid", references: [], conflicts: [], generated_at: "2026-07-26T00:00:00Z" } });
    expect((await askApi.history({ page: 2, page_size: 10 })).items).toHaveLength(1);
    expect((await askApi.detail("a1")).response.answer).toBe("answer");
    mock.restore();
  });
});

describe("authentication service", () => {
  it("uses the session-authentication contracts", async () => {
    const mock = new MockAdapter(http);
    mock.onPost("/api/auth/login").reply((config) => {
      expect(JSON.parse(config.data)).toEqual({ username: "radar", password: "CodeRadar1" });
      return [200, { user_id: "u1", username: "radar", created_at: "2026-07-26T00:00:00Z" }];
    });
    mock.onGet("/api/auth/me").reply(200, { user_id: "u1", username: "radar", created_at: "2026-07-26T00:00:00Z" });
    expect((await authApi.login({ username: "radar", password: "CodeRadar1" })).username).toBe("radar");
    expect((await authApi.me()).user_id).toBe("u1");
    mock.restore();
  });
});

describe("Mini-RAG service", () => {
  it("posts a retrieval-only query contract", async () => {
    const mock = new MockAdapter(http);
    mock.onPost("/api/rag/query").reply((config) => {
      const payload = JSON.parse(config.data);
      expect(payload).toEqual({ question: "Cursor Agent 更新", competitor: "Cursor", top_k: 10 });
      return [200, {
        query_id: "q1",
        query: payload.question,
        parsed_filters: { competitor: "Cursor" },
        evidence: [],
        conflicts: [],
        retrieval_trace: {
          bm25_candidates: 0,
          dense_candidates: 0,
          fused_candidates: 0,
          reranked_candidates: 0,
          returned_candidates: 0,
          latency_ms: 5,
          stage_latency_ms: {},
          warnings: [],
          retrieval_config: {}
        }
      }];
    });
    const response = await ragApi.query({ question: "Cursor Agent 更新", competitor: "Cursor", top_k: 10 });
    expect(response.query_id).toBe("q1");
    mock.restore();
  });

  it("loads stored evidence-query results without re-running a search", async () => {
    const mock = new MockAdapter(http);
    mock.onGet("/api/rag/history").reply(200, { items: [{ query_id: "q1", question: "Cursor", result_count: 1, latency_ms: 5, created_at: "2026-07-26T00:00:00Z" }], page: 1, page_size: 10, total: 1, total_pages: 1 });
    mock.onGet("/api/rag/history/q1").reply(200, { query_id: "q1", request: { question: "Cursor", top_k: 10 }, response: { query_id: "q1", query: "Cursor", parsed_filters: {}, evidence: [], conflicts: [], retrieval_trace: { bm25_candidates: 0, dense_candidates: 0, fused_candidates: 0, reranked_candidates: 0, returned_candidates: 0, latency_ms: 5, stage_latency_ms: {}, warnings: [], retrieval_config: {} } } });
    expect((await ragApi.history()).items[0].result_count).toBe(1);
    expect((await ragApi.detail("q1")).response.query_id).toBe("q1");
    mock.restore();
  });
});

describe("admin service", () => {
  it("loads a normalized paginated document list", async () => {
    const mock = new MockAdapter(http);
    mock.onGet("/api/admin/documents").reply((config) => {
      expect(config.params).toEqual({
        page: 2,
        page_size: 20,
        q: "Agent",
        status: "current",
        competitor: "Cursor"
      });
      return [200, { items: [], page: 2, page_size: 20, total: 21, total_pages: 2 }];
    });
    const response = await adminApi.documents({
      page: 2,
      page_size: 20,
      q: "Agent",
      status: "current",
      competitor: "Cursor",
      source_type: ""
    });
    expect(response.total_pages).toBe(2);
    mock.restore();
  });

  it("posts a multipart document with the protected import metadata", async () => {
    const mock = new MockAdapter(http);
    mock.onPost("/api/admin/documents/import").reply((config) => {
      const body = config.data as FormData;
      expect(body.get("file")).toBeInstanceOf(File);
      expect(body.get("competitor")).toBe("Cursor");
      expect(body.get("dimension_tags")).toBe('["agent_context"]');
      expect(body.get("source_type")).toBe("product_docs");
      return [200, {
        status: "success",
        persisted: true,
        documents_imported: 1,
        documents_skipped: 0,
        chunks_generated: 1,
        chunks_indexed: 1,
        chunks_skipped: 0,
        indexed_chunks_total: 998,
        warnings: [],
        errors: []
      }];
    });
    const response = await adminApi.importDocuments(
      new File(["guide"], "guide.md"),
      {
        competitor: "Cursor",
        title: "Agent guide",
        dimension_tags: ["agent_context"],
        source_type: "product_docs",
        evidence_level: "C"
      }
    );
    expect(response.indexed_chunks_total).toBe(998);
    mock.restore();
  });
});
