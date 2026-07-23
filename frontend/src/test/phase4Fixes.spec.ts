import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AskView from "../views/AskView.vue";
import { askApi } from "../api/services";
import { useAppStore } from "../stores/app";
import { analysisTimeLabel, buildAnalysisWindow, presetFromCorrelationId } from "../utils/analysisTime";
import type { AskResponse } from "../types/api";

const answer: AskResponse = {
    ask_id: "ask_1",
    query_id: "query_1",
    answer: "出现了新的 Agent 能力 [1]。",
    answer_mode: "hybrid",
    references: [{
      chunk_id: "chunk_1",
      document_id: "document_1",
      version_id: "version_1",
      title: "Agent update",
      url: "https://example.test/agent",
      competitor: "Cursor",
      source_type: "official_changelog",
      evidence_level: "A",
      dimension_tags: ["agent_context"]
    }],
    conflicts: [],
    generated_at: "2026-07-22T00:00:00Z"
};

describe("ephemeral Ask conversation", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  function mountAsk() {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useAppStore();
    store.competitors = [{
      id: "cursor",
      name: "Cursor",
      aliases: [],
      sources: {},
      enabled: true,
      config_version: 1,
      created_at: "2026-07-22T00:00:00Z",
      updated_at: "2026-07-22T00:00:00Z"
    }];

    return mount(AskView, { global: { plugins: [pinia, ElementPlus] } });
  }

  it("appends a question, answer, and references without persistent storage", async () => {
    vi.spyOn(askApi, "ask").mockResolvedValue(answer);
    const wrapper = mountAsk();
    await wrapper.find("textarea").setValue("最近有什么更新？");
    await wrapper.find("textarea").trigger("keydown", { key: "Enter" });
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await flushPromises();

    expect(wrapper.find(".chat-user__bubble").text()).toContain("最近有什么更新？");
    expect(wrapper.find(".answer-text").text()).toBe(answer.answer);
    expect(wrapper.find(".reference-list").text()).toContain("Agent update");
    expect(localStorage.length).toBe(0);
  });

  it("starts empty on every mount and keeps Shift+Enter as a newline", async () => {
    localStorage.setItem("coderadar-ask-history-v1", JSON.stringify([answer]));
    const spy = vi.spyOn(askApi, "ask").mockResolvedValue(answer);
    const wrapper = mountAsk();
    expect(wrapper.find(".ask-empty").exists()).toBe(true);
    await wrapper.find("textarea").setValue("两行问题");
    await wrapper.find("textarea").trigger("keydown", { key: "Enter", shiftKey: true });
    await flushPromises();
    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.find(".chat-turn").exists()).toBe(false);
  });
});

describe("analysis time presets", () => {
  const now = new Date("2026-07-22T12:00:00.000Z");

  it("builds UTC windows for week, month, and half-year presets", () => {
    expect(buildAnalysisWindow("7d", now)).toEqual({
      start_time: "2026-07-15T12:00:00.000Z",
      end_time: "2026-07-22T12:00:00.000Z"
    });
    expect(buildAnalysisWindow("30d", now).start_time).toBe("2026-06-22T12:00:00.000Z");
    expect(buildAnalysisWindow("180d", now).start_time).toBe("2026-01-23T12:00:00.000Z");
  });

  it("omits timestamps for all-time and recovers labels from workflow correlation IDs", () => {
    expect(buildAnalysisWindow("all", now)).toEqual({});
    expect(presetFromCorrelationId("web-30d-1234")).toBe("30d");
    expect(analysisTimeLabel(presetFromCorrelationId("web-30d-1234"))).toBe("近一个月");
    expect(presetFromCorrelationId("legacy-id")).toBeNull();
  });
});
