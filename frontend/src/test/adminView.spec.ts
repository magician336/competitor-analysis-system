import { flushPromises, mount, RouterLinkStub } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AdminView from "../views/AdminView.vue";
import { adminApi, competitorApi, systemApi } from "../api/services";
import type { AdminImportResponse, AdminOverview, ReadyStatus } from "../types/api";

const ready: ReadyStatus = {
  status: "ready",
  index: "coderadar_chunks_current",
  physical_index: "coderadar_chunks_v20260725",
  indexed_chunks: 997,
  embedding_model: "BAAI/bge-m3",
  embedding_dimension: 1024,
  index_embedding_model: "BAAI/bge-m3",
  index_embedding_dimension: 1024,
  embedding_compatible: true,
  backend: { status: "green", number_of_nodes: 1, active_shards: 2, unassigned_shards: 0 }
};

const overview: AdminOverview = {
  dataset_updated_at: "2026-07-25T14:32:00Z",
  stats: {
    documents_total: 86,
    current_versions: 81,
    historical_versions: 5,
    pending_review: 2
  },
  distributions: {
    competitors: { Cursor: 42, "GitHub Copilot": 26 },
    source_types: { product_docs: 52, official_changelog: 34 }
  },
  recent_documents: [{
    document_id: "doc_cursor_agent",
    version_id: "ver_cursor_agent_2",
    title: "Cursor Agent product guide",
    competitor: "Cursor",
    source_type: "product_docs",
    publish_time: "2026-07-24T00:00:00Z",
    is_current: true
  }]
};

const importResponse: AdminImportResponse = {
  status: "success",
  persisted: true,
  documents_imported: 1,
  documents_skipped: 0,
  chunks_generated: 4,
  chunks_indexed: 4,
  chunks_skipped: 0,
  indexed_chunks_total: 1001,
  warnings: [],
  errors: []
};

function mockApis() {
  vi.spyOn(systemApi, "health").mockResolvedValue({ status: "ok" });
  vi.spyOn(systemApi, "ready").mockResolvedValue(ready);
  vi.spyOn(adminApi, "overview").mockResolvedValue(overview);
  vi.spyOn(competitorApi, "list").mockResolvedValue(
    ["CodeGeeX", "Cursor", "GitHub Copilot", "通义灵码", "Trae"].map((name, index) => ({
      id: `competitor-${index + 1}`,
      name,
      aliases: [`${name} alias`],
      sources: {},
      enabled: true,
      config_version: 1,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z"
    }))
  );
}

function mountAdmin() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(AdminView, {
    attachTo: document.body,
    global: {
      plugins: [pinia, ElementPlus],
      stubs: { RouterLink: RouterLinkStub }
    }
  });
}

async function attachFile(wrapper: ReturnType<typeof mountAdmin>, file: File) {
  const input = wrapper.find('input[type="file"]');
  Object.defineProperty(input.element, "files", { configurable: true, value: [file] });
  await input.trigger("change");
}

describe("admin management page", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockApis();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
  });

  it("renders real service, content, and the compact configuration telemetry section", async () => {
    const wrapper = mountAdmin();
    await flushPromises();

    expect(wrapper.find(".admin-service-grid").text()).toContain("Mini-RAG");
    expect(wrapper.find(".admin-service-grid").text()).toContain("997 chunks");
    expect(wrapper.find(".admin-metrics").text()).toContain("86");
    expect(wrapper.find(".admin-metrics").text()).toContain("待审核");
    expect(wrapper.find(".distribution-list").text()).toContain("Cursor");
    expect(wrapper.find(".admin-distribution").text()).toContain("产品文档");
    expect(wrapper.find(".admin-recent").exists()).toBe(false);
    expect(wrapper.find("#admin-operations-heading").text()).toBe("配置与监控");
    expect(wrapper.findAll(".competitor-list li")).toHaveLength(5);
    expect(wrapper.findAll(".dimension-list li")).toHaveLength(7);
    expect(wrapper.find(".dimension-list").text()).toContain("D7");
    expect(wrapper.findAll(".monitor-grid > div")).toHaveLength(6);
    expect(wrapper.find(".monitor-grid").text()).toContain("coderadar_chunks_v20260725");
    expect(wrapper.find("#admin-classification-heading").exists()).toBe(false);
    expect(wrapper.find("#admin-monitor-heading").exists()).toBe(false);
    wrapper.unmount();
  });

  it("links the primary document CTA and each content metric to the document library", async () => {
    const wrapper = mountAdmin();
    await flushPromises();
    const links = wrapper.find(".admin-metrics").findAllComponents(RouterLinkStub);
    const primaryLink = wrapper.findAllComponents(RouterLinkStub)
      .find((link) => link.classes().includes("admin-documents-cta"));

    expect(primaryLink?.props("to")).toBe("/admin/documents");
    expect(links).toHaveLength(4);
    expect(links[0].props("to")).toBe("/admin/documents");
    expect(links[1].props("to")).toEqual({ path: "/admin/documents", query: { status: "current" } });
    expect(links[2].props("to")).toEqual({ path: "/admin/documents", query: { status: "historical" } });
    expect(links[3].props("to")).toEqual({ path: "/admin/documents", query: { status: "review" } });
    wrapper.unmount();
  });

  it("keeps content, compact operations, and import sections in their intended order", async () => {
    const wrapper = mountAdmin();
    await flushPromises();
    const headings = wrapper.findAll(".admin-section__heading h2").map((heading) => heading.text());

    expect(headings).toEqual(["内容概览", "配置与监控", "导入新文档"]);
    wrapper.unmount();
  });

  it("refreshes overview automatically every 30 seconds", async () => {
    vi.useFakeTimers();
    const wrapper = mountAdmin();
    await flushPromises();
    expect(adminApi.overview).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(30_000);
    await flushPromises();
    expect(adminApi.overview).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it("uploads a text document with metadata and renders the indexing result", async () => {
    const upload = vi.spyOn(adminApi, "importDocuments").mockImplementation(async (_file, _metadata, onProgress) => {
      onProgress?.(100);
      return importResponse;
    });
    const wrapper = mountAdmin();
    await flushPromises();
    await attachFile(wrapper, new File(["Agent release notes"], "agent-guide.md", { type: "text/markdown" }));

    const selects = wrapper.findAllComponents({ name: "ElSelect" });
    await selects[0].setValue("Cursor");
    await wrapper.find(".import-submit").trigger("click");
    await flushPromises();

    expect(upload).toHaveBeenCalledWith(
      expect.objectContaining({ name: "agent-guide.md" }),
      expect.objectContaining({
        competitor: "Cursor",
        title: "agent-guide",
        source_type: "product_docs",
        evidence_level: "C"
      }),
      expect.any(Function)
    );
    expect(wrapper.find(".import-result").text()).toContain("文档已导入并完成索引");
    expect(wrapper.find(".import-result").text()).toContain("1,001");
    wrapper.unmount();
  });

  it("uses file-owned metadata for JSONL and exposes partial index failure", async () => {
    const result: AdminImportResponse = {
      ...importResponse,
      status: "partial_failure",
      persisted: true,
      chunks_indexed: 0,
      indexed_chunks_total: null,
      errors: ["Incremental indexing failed"]
    };
    const upload = vi.spyOn(adminApi, "importDocuments").mockResolvedValue(result);
    const wrapper = mountAdmin();
    await flushPromises();
    await attachFile(wrapper, new File(['{"document_id":"doc_1"}'], "records.jsonl", { type: "application/x-ndjson" }));

    expect(wrapper.find(".jsonl-notice").text()).toContain("读取文件内的标准字段");
    await wrapper.find(".import-submit").trigger("click");
    await flushPromises();

    expect(upload.mock.calls[0][1]).toEqual({});
    expect(wrapper.find(".import-result.partial").text()).toContain("文档已保存，索引需要重试");
    expect(wrapper.find(".import-result.partial").text()).toContain("Incremental indexing failed");
    wrapper.unmount();
  });

  it("rejects unsupported files before contacting the import endpoint", async () => {
    const upload = vi.spyOn(adminApi, "importDocuments").mockResolvedValue(importResponse);
    const wrapper = mountAdmin();
    await flushPromises();
    await attachFile(wrapper, new File(["pdf"], "guide.pdf", { type: "application/pdf" }));

    expect(wrapper.find(".import-result.failed").text()).toContain("格式不支持");
    expect(upload).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});
