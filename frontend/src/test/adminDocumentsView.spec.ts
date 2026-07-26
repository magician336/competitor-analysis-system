import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AdminDocumentsView from "../views/AdminDocumentsView.vue";
import { adminApi, competitorApi } from "../api/services";
import type { AdminDocumentListItem, AdminOverview, Page } from "../types/api";

const documents: AdminDocumentListItem[] = [{
  document_id: "doc_cursor_agent",
  version_id: "ver_cursor_agent_2",
  title: "Cursor Agent product guide",
  competitor: "Cursor",
  source_type: "product_docs",
  publish_time: "2026-07-24T00:00:00Z",
  dimension_tags: ["agent_context"],
  is_current: true,
  needs_review: true
}];

const page: Page<AdminDocumentListItem> = {
  items: documents,
  page: 1,
  page_size: 20,
  total: 41,
  total_pages: 3
};

const overview: AdminOverview = {
  dataset_updated_at: "2026-07-25T14:32:00Z",
  stats: { documents_total: 86, current_versions: 81, historical_versions: 5, pending_review: 2 },
  distributions: {
    competitors: { Cursor: 42, "GitHub Copilot": 26 },
    source_types: { product_docs: 52, official_changelog: 34 }
  },
  recent_documents: []
};

async function mountDocuments(query = "") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/admin", component: { template: "<div />" } },
      { path: "/admin/documents", component: AdminDocumentsView }
    ]
  });
  await router.push(`/admin/documents${query}`);
  await router.isReady();
  const wrapper = mount(AdminDocumentsView, {
    attachTo: document.body,
    global: { plugins: [pinia, router, ElementPlus] }
  });
  await flushPromises();
  return { wrapper, router };
}

describe("admin document browsing", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(adminApi, "documents").mockResolvedValue(page);
    vi.spyOn(adminApi, "overview").mockResolvedValue(overview);
    vi.spyOn(competitorApi, "list").mockResolvedValue([]);
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("loads the current query and renders summary-only rows", async () => {
    const { wrapper } = await mountDocuments("?status=current&q=Agent&competitor=Cursor&source_type=product_docs");

    expect(adminApi.documents).toHaveBeenLastCalledWith({
      page: 1,
      page_size: 20,
      q: "Agent",
      status: "current",
      competitor: "Cursor",
      source_type: "product_docs"
    });
    expect(wrapper.find(".document-table").text()).toContain("Cursor Agent product guide");
    expect(wrapper.find(".document-table").text()).toContain("D2 · Agent 与上下文");
    expect(wrapper.find(".document-table").text()).toContain("待审核");
    wrapper.unmount();
  });

  it("updates the URL and resets pagination when a title search is submitted", async () => {
    const { wrapper, router } = await mountDocuments("?page=3");
    await wrapper.find('input[aria-label="按文档标题搜索"]').setValue("  Copilot  ");
    await wrapper.find(".document-search").trigger("submit");
    await flushPromises();

    expect(router.currentRoute.value.query).toEqual({ q: "Copilot" });
    expect(adminApi.documents).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 1,
      page_size: 20,
      q: "Copilot",
      status: "all"
    }));
    wrapper.unmount();
  });

  it("requests the selected page while preserving active filters", async () => {
    const { wrapper, router } = await mountDocuments("?status=review&q=Agent");
    const pagination = wrapper.findComponent({ name: "ElPagination" });
    pagination.vm.$emit("current-change", 2);
    await flushPromises();

    expect(router.currentRoute.value.query).toEqual({ status: "review", q: "Agent", page: "2" });
    expect(adminApi.documents).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 2,
      status: "review",
      q: "Agent"
    }));
    wrapper.unmount();
  });

  it("shows empty and error states", async () => {
    vi.mocked(adminApi.documents).mockResolvedValueOnce({
      items: [], page: 1, page_size: 20, total: 0, total_pages: 0
    });
    const empty = await mountDocuments("?q=missing");
    expect(empty.wrapper.find(".document-state").text()).toContain("没有找到符合条件的文档");
    empty.wrapper.unmount();

    vi.mocked(adminApi.documents).mockRejectedValueOnce(new Error("network unavailable"));
    const failed = await mountDocuments();
    expect(failed.wrapper.find('[role="alert"]').text()).toContain("文档暂时无法加载");
    failed.wrapper.unmount();
  });
});
