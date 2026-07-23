import { describe, expect, it } from "vitest";
import MockAdapter from "axios-mock-adapter";
import { askApi, cleanQuery } from "../api/services";
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
});
