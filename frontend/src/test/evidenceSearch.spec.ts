import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EvidenceSearchView from "../views/EvidenceSearchView.vue";
import { ragApi } from "../api/services";
import { useAppStore } from "../stores/app";
import type { RAGResponse } from "../types/api";

const response: RAGResponse = {
  query_id: "qry_evidence_1",
  query: "Cursor 最近有哪些 Agent 更新？",
  parsed_filters: {
    competitor: "Cursor",
    dimension_tags: ["agent_context"],
    current_only: true
  },
  evidence: [{
    citation_id: "cite_1",
    chunk_id: "chunk_1",
    document_id: "document_1",
    version_id: "version_1",
    title: "Cursor Agent update",
    content: "Cursor 发布了新的 Agent 能力，并扩展了上下文处理方式。",
    quote: "Cursor 发布了新的 Agent 能力。",
    url: "https://example.test/cursor-agent",
    heading_path: ["Changelog", "Agent"],
    competitor: "Cursor",
    source_type: "official_changelog",
    evidence_level: "A",
    dimension_tags: ["agent_context"],
    publish_time: "2026-07-20T00:00:00Z",
    is_current: true,
    final_score: 0.81,
    retrieval_methods: ["bm25", "dense"]
  }],
  conflicts: [{
    conflict_id: "conflict_1",
    field: "availability",
    competitor: "Cursor",
    values: ["beta", "general availability"],
    chunk_ids: ["chunk_1", "chunk_2"],
    preferred_chunk_id: "chunk_1",
    reason: "newer evidence"
  }],
  retrieval_trace: {
    bm25_candidates: 20,
    dense_candidates: 20,
    fused_candidates: 30,
    reranked_candidates: 20,
    returned_candidates: 1,
    latency_ms: 126.4,
    stage_latency_ms: { bm25: 12 },
    warnings: [],
    retrieval_config: {}
  }
};

function mountEvidence() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useAppStore();
  store.ready = {
    status: "ready",
    index: "coderadar_chunks_current",
    indexed_chunks: 997,
    embedding_compatible: true,
    backend: { status: "green", number_of_nodes: 1 }
  };
  store.apiOnline = true;
  store.competitors = [{
    id: "cursor",
    name: "Cursor",
    aliases: [],
    sources: {},
    enabled: true,
    config_version: 1,
    created_at: "2026-07-20T00:00:00Z",
    updated_at: "2026-07-20T00:00:00Z"
  }];
  return mount(EvidenceSearchView, {
    attachTo: document.body,
    global: { plugins: [pinia, ElementPlus] }
  });
}

describe("evidence retrieval page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("omits empty filters, fixes top_k to 10, and renders evidence without an AI answer", async () => {
    const query = vi.spyOn(ragApi, "query").mockResolvedValue(response);
    const wrapper = mountEvidence();
    await wrapper.find("textarea").setValue(response.query);
    await wrapper.find(".evidence-query__submit").trigger("click");
    await flushPromises();

    expect(query).toHaveBeenCalledWith({ question: response.query, top_k: 10 });
    expect(wrapper.find(".evidence-list").text()).toContain("Cursor Agent update");
    expect(wrapper.find(".evidence-intelligence").text()).toContain("Agent 与上下文");
    expect(wrapper.find(".evidence-conflict").text()).toContain("1 组证据冲突");
    expect(wrapper.find(".evidence-intelligence").text()).not.toContain("证据总量");
    expect(wrapper.find(".evidence-intelligence").text()).not.toContain("返回结果");
    expect(wrapper.find(".evidence-intelligence__latency").text()).toContain("126");
    expect(wrapper.find(".evidence-results h2").text()).toBe("检索结果");
    expect(wrapper.find(".evidence-list__body > span").text()).toBe("Cursor · 官方更新");
    expect(wrapper.find(".evidence-list__meta").text()).toContain("Agent 与上下文");
    expect(wrapper.find(".evidence-list__meta").text()).not.toContain("官方更新");
    expect(wrapper.findAll(".evidence-examples button")).toHaveLength(3);
    expect(wrapper.text()).not.toContain("AI 证据回答");
  });

  it("keeps three verified examples visible and applies their bound filters before searching", async () => {
    const query = vi.spyOn(ragApi, "query").mockResolvedValue({ ...response, conflicts: [] });
    const wrapper = mountEvidence();

    expect(wrapper.find(".evidence-intelligence").exists()).toBe(false);
    const exampleButtons = wrapper.findAll(".evidence-examples button");
    expect(exampleButtons).toHaveLength(3);
    expect(exampleButtons[0].text()).toContain("Cursor Mobile App");
    expect(exampleButtons[2].text()).toContain("GitHub Copilot");

    await exampleButtons[0].trigger("click");
    await flushPromises();

    expect(query).toHaveBeenCalledWith({
      question: "Cursor Mobile App 如何在手机上启动和管理 Cloud Agent？",
      competitor: "Cursor",
      dimension_tags: ["agent_context"],
      top_k: 10
    });
    expect(wrapper.findAll(".evidence-examples button")).toHaveLength(3);
    expect(wrapper.find(".evidence-intelligence").exists()).toBe(true);
  });

  it("provides explicit all options for competitor and dimensions", () => {
    const wrapper = mountEvidence();
    const options = wrapper.findAllComponents({ name: "ElOption" });
    expect(options.some((option) => option.props("label") === "全部竞品")).toBe(true);
    expect(options.some((option) => option.props("label") === "全部维度")).toBe(true);
  });

  it("sends explicit competitor, time, and dimensions as overrides", async () => {
    const query = vi.spyOn(ragApi, "query").mockResolvedValue({ ...response, conflicts: [] });
    const wrapper = mountEvidence();
    const selects = wrapper.findAllComponents({ name: "ElSelect" });
    await selects[0].setValue("Cursor");
    await selects[1].setValue("7d");
    await selects[2].setValue(["agent_context", "security_compliance"]);
    await wrapper.find("textarea").setValue("查找最近的官方证据");
    await wrapper.find(".evidence-query__submit").trigger("click");
    await flushPromises();

    const payload = query.mock.calls[0][0];
    expect(payload.question).toBe("查找最近的官方证据");
    expect(payload.top_k).toBe(10);
    expect(payload.competitor).toBe("Cursor");
    expect(payload.dimension_tags).toEqual(["agent_context", "security_compliance"]);
    expect(payload.start_time).toEqual(expect.any(String));
    expect(payload.end_time).toEqual(expect.any(String));
  });

  it("opens a source-locatable detail drawer from a result row", async () => {
    vi.spyOn(ragApi, "query").mockResolvedValue(response);
    const wrapper = mountEvidence();
    await wrapper.find("textarea").setValue(response.query);
    await wrapper.find(".evidence-query__submit").trigger("click");
    await flushPromises();
    await wrapper.find(".evidence-list article").trigger("click");
    await flushPromises();

    expect(document.body.textContent).toContain("证据详情");
    expect(document.body.textContent).toContain("chunk_1");
    expect(document.body.textContent).toContain("bm25 + dense");
    expect(document.body.querySelector('a[href="https://example.test/cursor-agent"]')).not.toBeNull();
  });

  it("shows at least three dimensions in the result rail and collapses the remainder", async () => {
    vi.spyOn(ragApi, "query").mockResolvedValue({
      ...response,
      conflicts: [],
      evidence: [{
        ...response.evidence[0],
        dimension_tags: ["agent_context", "ide_ecosystem", "security_compliance", "model_extensibility"]
      }]
    });
    const wrapper = mountEvidence();
    await wrapper.find("textarea").setValue(response.query);
    await wrapper.find(".evidence-query__submit").trigger("click");
    await flushPromises();

    const tags = wrapper.findAll(".evidence-list__meta-tags small");
    expect(tags).toHaveLength(4);
    expect(tags[0].text()).toBe("Agent 与上下文");
    expect(tags[1].text()).toBe("IDE 生态");
    expect(tags[2].text()).toBe("安全合规");
    expect(tags[3].text()).toBe("+1");
  });
});
