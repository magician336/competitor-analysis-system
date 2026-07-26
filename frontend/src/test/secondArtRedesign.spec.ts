import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { workflowApi, briefingApi, snapshotApi } from "../api/services";
import { useAppStore } from "../stores/app";
import AnalysisView from "../views/AnalysisView.vue";
import AnalysisResultView from "../views/AnalysisResultView.vue";
import AnalysisReportView from "../views/AnalysisReportView.vue";
import type { BriefingDetail, WorkflowDetail } from "../types/api";

const completedWorkflow: WorkflowDetail = {
  workflow_id: "workflow_art_direction",
  competitor: "Cursor",
  analysis_mode: "hybrid",
  status: "success",
  progress: 100,
  attempt_count: 1,
  max_attempts: 2,
  partial_failure: false,
  cancel_requested: false,
  branches: [
    { branch: "product", status: "success", progress: 100, last_attempt: 1, card_ids: [], trace_id: "trace_product" },
    { branch: "price", status: "success", progress: 100, last_attempt: 1, card_ids: [], trace_id: "trace_price" },
    { branch: "risk", status: "success", progress: 100, last_attempt: 1, card_ids: [], trace_id: "trace_risk" },
  ],
  card_ids: [],
  snapshot_id: null,
  briefing_id: "briefing_art_direction",
};

const briefing: BriefingDetail = {
  briefing_id: "briefing_art_direction",
  competitor: "Cursor",
  workflow_id: completedWorkflow.workflow_id,
  snapshot_id: null,
  created_at: "2026-07-22T10:00:00Z",
  markdown: "# Cursor 模型趋势分析\n\n这里是 Markdown 正文。",
};

function setupStore() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const store = useAppStore();
  store.competitors = [{
    id: "cursor", name: "Cursor", aliases: [], sources: {}, enabled: true,
    config_version: 1, created_at: "2026-07-22T00:00:00Z", updated_at: "2026-07-22T00:00:00Z",
  }];
  return { pinia, store };
}

describe("mode-first analysis wizard", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("moves through four steps, preserves values, and submits the selected mode", async () => {
    const { pinia, store } = setupStore();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/analysis", component: AnalysisView },
        { path: "/analysis/:workflowId", component: { template: "<div>result</div>" } },
      ],
    });
    await router.push("/analysis");
    await router.isReady();
    const submit = vi.spyOn(workflowApi, "submit").mockResolvedValue({
      workflow_id: "workflow_new", status: "queued", deduplicated: false,
      status_url: "/api/workflows/workflow_new",
    });
    const wrapper = mount(AnalysisView, { global: { plugins: [pinia, router, ElementPlus] } });
    await flushPromises();

    await wrapper.get('[data-test="analysis-mode-llm"]').trigger("click");
    expect(store.agentMode).toBe("llm");
    expect(wrapper.get(".analysis-page").attributes("data-analysis-mode")).toBe("llm");

    await wrapper.get('[data-test="wizard-next"]').trigger("click");
    expect(wrapper.get(".wizard-stage").attributes("data-wizard-step")).toBe("scope");
    (wrapper.vm as unknown as { form: { competitor: string } }).form.competitor = "Cursor";
    await wrapper.get('[data-test="wizard-next"]').trigger("click");
    expect(wrapper.get(".wizard-stage").attributes("data-wizard-step")).toBe("task");
    await wrapper.get('[data-test="wizard-next"]').trigger("click");
    expect(wrapper.get(".wizard-stage").attributes("data-wizard-step")).toBe("review");

    await wrapper.get('[data-test="wizard-back"]').trigger("click");
    expect(wrapper.get(".wizard-stage").attributes("data-wizard-step")).toBe("task");
    expect((wrapper.vm as unknown as { form: { competitor: string } }).form.competitor).toBe("Cursor");
    await wrapper.get('[data-test="wizard-next"]').trigger("click");
    await wrapper.get('[data-test="wizard-submit"]').trigger("click");
    await flushPromises();

    expect(submit).toHaveBeenCalledWith(expect.objectContaining({
      competitor: "Cursor",
      analysis_mode: "llm",
      include_snapshot: true,
      include_briefing: true,
    }));
    expect(router.currentRoute.value.path).toBe("/analysis/workflow_new");
  });
});

describe("Markdown-first result flow", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("replaces a completed workflow status page with the report route", async () => {
    const { pinia } = setupStore();
    vi.spyOn(workflowApi, "detail").mockResolvedValue(completedWorkflow);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/analysis/:workflowId", component: AnalysisResultView },
        { path: "/analysis/:workflowId/report", component: { template: "<div>report</div>" } },
      ],
    });
    await router.push(`/analysis/${completedWorkflow.workflow_id}`);
    await router.isReady();
    mount(AnalysisResultView, { global: { plugins: [pinia, router, ElementPlus] } });
    await flushPromises();
    expect(router.currentRoute.value.path).toBe(`/analysis/${completedWorkflow.workflow_id}/report`);
  });

  it("keeps Markdown on the right while artifacts remain in the left sidebar", async () => {
    const { pinia } = setupStore();
    vi.spyOn(workflowApi, "detail").mockResolvedValue(completedWorkflow);
    vi.spyOn(briefingApi, "detail").mockResolvedValue(briefing);
    vi.spyOn(snapshotApi, "detail").mockRejectedValue(new Error("no snapshot"));
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/analysis", component: { template: "<div>history</div>" } },
        { path: "/analysis/:workflowId/report", component: AnalysisReportView },
      ],
    });
    await router.push(`/analysis/${completedWorkflow.workflow_id}/report`);
    await router.isReady();
    const wrapper = mount(AnalysisReportView, { global: { plugins: [pinia, router, ElementPlus] } });
    await flushPromises();

    expect(wrapper.get(".report-sidebar").text()).toContain("能力快照");
    expect(wrapper.get(".report-sidebar").text()).toContain("高级运行详情");
    expect(wrapper.get(".report-reader .markdown-body").text()).toContain("Markdown 正文");
    expect(wrapper.find(".report-hero").exists()).toBe(false);
  });
});
