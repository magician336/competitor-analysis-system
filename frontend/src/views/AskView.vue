<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import { Loading, Plus, Top } from "@element-plus/icons-vue";
import { askApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import { ANALYSIS_TIME_OPTIONS, buildAnalysisWindow } from "../utils/analysisTime";
import type { AnalysisTimePreset, AskHistoryDetail, AskHistorySummary, AskResponse, AskTurn, Page } from "../types/api";

const store = useAppStore();
const submitting = ref(false);
const turns = ref<AskTurn[]>([]);
const conversation = ref<HTMLElement | null>(null);
const nearBottom = ref(true);
const activeTab = ref<"current" | "history">("current");
const history = ref<Page<AskHistorySummary> | null>(null);
const historyLoading = ref(false);
const historyError = ref("");
const restoringId = ref("");
const HISTORY_PAGE_SIZE = 10;
const presets = [
  "最近有哪些值得关注的 Agent 能力变化？",
  "Cursor Teams 套餐的月费和团队管理能力是什么？",
  "TRAE 沙箱权限设计为什么会阻断自动化任务？",
  "哪些能力方向正在成为行业标配？"
];
const form = reactive({
  question: "",
  target: "",
  topK: 8,
  timePreset: "all" as AnalysisTimePreset
});
const hasConversation = computed(() => turns.value.length > 0);

function historyRecord(item: AskHistorySummary | AskHistoryDetail) {
  return item as unknown as Record<string, unknown>;
}

function historyValue(item: AskHistorySummary | AskHistoryDetail, key: string) {
  return historyRecord(item)[key];
}

function historyText(item: AskHistorySummary | AskHistoryDetail, key: string, fallback = "") {
  const value = historyValue(item, key);
  return typeof value === "string" ? value : fallback;
}

function formatHistoryDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

async function loadHistory(page = history.value?.page || 1) {
  historyLoading.value = true;
  historyError.value = "";
  try {
    history.value = await askApi.history({ page, page_size: HISTORY_PAGE_SIZE });
  } catch (reason) {
    historyError.value = errorMessage(reason);
  } finally {
    historyLoading.value = false;
  }
}

function extractResponse(detail: AskHistoryDetail): AskResponse {
  const record = historyRecord(detail);
  const embedded = record.response;
  return (embedded && typeof embedded === "object" ? embedded : detail) as AskResponse;
}

async function restoreHistory(item: AskHistorySummary) {
  const askId = historyText(item, "ask_id");
  if (!askId || restoringId.value) return;
  restoringId.value = askId;
  try {
    const detail = await askApi.detail(askId);
    const restored: AskTurn = {
      turn_id: askId,
      question: detail.request.question,
      target: detail.request.analysis_target || "",
      time_preset: "all",
      status: "success",
      response: extractResponse(detail),
      created_at: detail.response.generated_at
    };
    turns.value = [restored];
    activeTab.value = "current";
    await scrollToLatest(true);
  } catch (reason) {
    historyError.value = errorMessage(reason);
  } finally {
    restoringId.value = "";
  }
}

function switchTab(tab: "current" | "history") {
  activeTab.value = tab;
  if (tab === "history" && !history.value && !historyLoading.value) void loadHistory();
}

function onScroll() {
  const element = conversation.value;
  if (!element) return;
  nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 140;
}

async function scrollToLatest(force = false) {
  await nextTick();
  const element = conversation.value;
  if (element && (force || nearBottom.value)) {
    if (typeof element.scrollTo === "function") element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    else element.scrollTop = element.scrollHeight;
  }
}

function newTurn(question: string): AskTurn {
  return {
    turn_id: `turn_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    question,
    target: form.target,
    time_preset: form.timePreset,
    status: "pending",
    created_at: new Date().toISOString()
  };
}

async function runTurn(turn: AskTurn) {
  submitting.value = true;
  turn.status = "pending";
  turn.error = undefined;
  await scrollToLatest(true);
  try {
    const analysisWindow = buildAnalysisWindow(turn.time_preset);
    const response: AskResponse = await askApi.ask({
      question: turn.question,
      analysis_target: turn.target || undefined,
      ...analysisWindow,
      top_k: form.topK
    });
    turn.response = response;
    turn.status = "success";
    if (history.value) void loadHistory(1);
  } catch (reason) {
    turn.error = errorMessage(reason);
    turn.status = "error";
  } finally {
    submitting.value = false;
    await scrollToLatest();
  }
}

async function submit(questionOverride?: string) {
  const question = (questionOverride ?? form.question).trim();
  if (!question || submitting.value) return;
  const turn = newTurn(question);
  turns.value.push(turn);
  form.question = "";
  await runTurn(turns.value[turns.value.length - 1]);
}

function handleEnter(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  void submit();
}

function retry(turn: AskTurn) {
  if (!submitting.value) void runTurn(turn);
}

function choosePreset(question: string) {
  form.question = question;
  void submit(question);
}

onMounted(async () => {
  if (!store.competitors.length) await store.loadCompetitors();
});
</script>

<template>
  <div class="ask-page grid-surface">
    <section ref="conversation" class="conversation" @scroll="onScroll">
      <nav class="history-tabs" aria-label="随问内容">
        <button type="button" :class="{ active: activeTab === 'current' }" @click="switchTab('current')">当前问答</button>
        <button type="button" :class="{ active: activeTab === 'history' }" @click="switchTab('history')">历史记录</button>
      </nav>

      <div v-if="activeTab === 'history'" class="ask-history">
        <div class="ask-history__heading">
          <div><p class="eyebrow">ASK ARCHIVE</p><h1>问答历史</h1></div>
          <button type="button" :disabled="historyLoading" @click="loadHistory(1)">刷新</button>
        </div>
        <div v-if="historyLoading" class="ask-history__state">正在读取历史记录…</div>
        <div v-else-if="historyError" class="ask-history__state ask-history__state--error">{{ historyError }}</div>
        <div v-else-if="history?.items.length" class="ask-history__list">
          <button
            v-for="item in history.items"
            :key="historyText(item, 'ask_id')"
            type="button"
            :disabled="Boolean(restoringId)"
            @click="restoreHistory(item)"
          >
            <span class="mono">{{ formatHistoryDate(item.generated_at) }}</span>
            <strong>{{ item.question || '未命名问答' }}</strong>
            <small>{{ item.analysis_target || '全部模型' }}</small>
            <em v-if="restoringId === historyText(item, 'ask_id')">恢复中…</em>
          </button>
        </div>
        <div v-else class="ask-history__state">暂时还没有问答记录。</div>
        <el-pagination
          v-if="history && history.total_pages > 1"
          class="history-pagination"
          background
          layout="prev, pager, next"
          :current-page="history.page"
          :page-count="history.total_pages"
          @current-change="loadHistory"
        />
      </div>

      <div v-else-if="!hasConversation" class="ask-empty">
        <span class="ask-empty__signal" aria-hidden="true"><i /><i /><i /></span>
        <p class="eyebrow">EVIDENCE-GROUNDED ASK</p>
        <h1>今天想观察什么变化？</h1>
        <p>询问模型能力、价格与风险信号。回答中的判断均来自可追溯的公开证据。</p>
        <div class="ask-presets" aria-label="预设问题">
          <button v-for="item in presets" :key="item" type="button" @click="choosePreset(item)">{{ item }} <span>↗</span></button>
        </div>
      </div>

      <div v-else class="conversation__stream" aria-live="polite">
        <article v-for="turn in turns" :key="turn.turn_id" class="chat-turn">
          <div class="chat-user">
            <div class="chat-user__bubble">{{ turn.question }}</div>
            <small v-if="turn.target">{{ turn.target }}</small>
          </div>
          <div class="chat-assistant">
            <span class="chat-assistant__mark" role="img" aria-label="CodeRadar AI">
              <i aria-hidden="true" /><i aria-hidden="true" /><i aria-hidden="true" />
            </span>
            <div class="chat-assistant__body">
              <div v-if="turn.status === 'pending'" class="answer-loading">
                <el-icon class="is-loading"><Loading /></el-icon>
                <span>正在检索证据并整理回答</span><i /><i /><i />
              </div>
              <template v-else-if="turn.status === 'success' && turn.response">
                <div class="answer-text">{{ turn.response.answer }}</div>
                <div class="answer-meta">
                  <span>{{ turn.response.answer_mode === "hybrid" ? "AI 证据回答" : "规则回退回答" }}</span>
                  <span>{{ turn.response.references.length }} 个参考来源</span>
                  <span class="mono">{{ turn.response.query_id }}</span>
                </div>
                <p v-if="turn.response.conflicts.length" class="inline-warning">不同来源中存在信息冲突，请结合原文判断。</p>
                <details v-if="turn.response.references.length" class="reference-disclosure">
                  <summary>参考来源 {{ turn.response.references.length }} <span>展开查看</span></summary>
                  <div class="reference-list">
                    <article v-for="(reference, index) in turn.response.references" :key="reference.chunk_id">
                      <span class="reference-index">[{{ index + 1 }}]</span>
                      <div>
                        <strong>{{ reference.title }}</strong>
                        <p>{{ reference.quote || reference.content || "暂无引用片段" }}</p>
                        <small>{{ reference.competitor }} · {{ reference.evidence_level }} · {{ reference.source_type }} · {{ reference.publish_time || "时间未知" }}</small>
                        <a :href="reference.url" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a>
                      </div>
                    </article>
                  </div>
                </details>
                <p v-else class="inline-note">当前没有足够的可引用证据。</p>
              </template>
              <div v-else class="turn-error">
                <strong>这次回答没有完成</strong>
                <span>{{ turn.error }}</span>
                <button type="button" @click="retry(turn)">重新发送</button>
              </div>
            </div>
          </div>
        </article>
      </div>
    </section>

    <div v-if="activeTab === 'current'" class="composer-dock">
      <div class="chat-composer" :class="{ 'chat-composer--active': form.question.trim() }">
        <el-popover placement="top-start" :width="340" trigger="click" popper-class="ask-options-popover">
          <template #reference>
            <button class="composer-icon" type="button" aria-label="设置问答范围"><el-icon><Plus /></el-icon></button>
          </template>
          <div class="composer-options">
            <label>分析对象</label>
            <el-select v-model="form.target" clearable placeholder="全部模型">
              <el-option v-for="item in store.enabledCompetitors" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
            <label>时间范围</label>
            <el-radio-group v-model="form.timePreset" size="small">
              <el-radio-button v-for="item in ANALYSIS_TIME_OPTIONS" :key="item.value" :value="item.value">{{ item.label }}</el-radio-button>
            </el-radio-group>
            <label>检索证据数</label>
            <el-slider v-model="form.topK" :min="3" :max="20" show-input />
          </div>
        </el-popover>
        <el-input
          v-model="form.question"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 6 }"
          maxlength="2000"
          resize="none"
          placeholder="询问模型趋势、能力变化或风险信号…"
          @keydown="handleEnter"
        />
        <button class="composer-send" type="button" :disabled="!form.question.trim() || submitting" aria-label="发送问题" @click="submit()">
          <el-icon><Top /></el-icon>
        </button>
      </div>
      <div class="composer-context">
        <span>{{ form.target || "全部模型" }}</span>
        <span>·</span><span>{{ ANALYSIS_TIME_OPTIONS.find(item => item.value === form.timePreset)?.label }}</span>
        <span>·</span><span>检索证据 {{ form.topK }} 条</span>
      </div>
    </div>
  </div>
</template>
