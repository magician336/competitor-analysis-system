<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ragApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import { ANALYSIS_TIME_OPTIONS, buildAnalysisWindow } from "../utils/analysisTime";
import {
  DIMENSIONS,
  type AnalysisTimePreset,
  type Dimension,
  type EvidenceHistoryDetail,
  type EvidenceHistorySummary,
  type EvidenceReference,
  type Page,
  type RAGQueryRequest,
  type RAGResponse
} from "../types/api";

type SearchStatus = "ready" | "searching" | "success" | "failed";
type DimensionSelection = Dimension | "__all__";
type ExampleQuery = {
  id: string;
  question: string;
  competitor: string;
  timePreset: AnalysisTimePreset;
  dimensions: Dimension[];
};

const store = useAppStore();
const form = reactive({
  question: "",
  competitor: "",
  timePreset: "all" as AnalysisTimePreset,
  dimensions: [] as DimensionSelection[]
});
const status = ref<SearchStatus>("ready");
const response = ref<RAGResponse | null>(null);
const error = ref("");
const selectedEvidence = ref<EvidenceReference | null>(null);
const drawerOpen = ref(false);
const copied = ref(false);
const activeTab = ref<"current" | "history">("current");
const history = ref<Page<EvidenceHistorySummary> | null>(null);
const historyLoading = ref(false);
const historyError = ref("");
const restoringId = ref("");
const HISTORY_PAGE_SIZE = 10;

const examples: ExampleQuery[] = [
  {
    id: "cursor-mobile",
    question: "Cursor Mobile App 如何在手机上启动和管理 Cloud Agent？",
    competitor: "Cursor",
    timePreset: "all",
    dimensions: ["agent_context"]
  },
  {
    id: "cursor-slack",
    question: "Cursor 在 Slack 中如何进行多仓库和跨频道 Agent 工作流？",
    competitor: "Cursor",
    timePreset: "all",
    dimensions: ["agent_context"]
  },
  {
    id: "copilot-jetbrains",
    question: "GitHub Copilot 在 JetBrains 中新增了哪些 BYOK 和 Agent 能力？",
    competitor: "GitHub Copilot",
    timePreset: "all",
    dimensions: ["ide_ecosystem"]
  }
];

const dimensionNames = Object.fromEntries(DIMENSIONS.map((item) => [item.key, item.name])) as Record<Dimension, string>;
const eventNames: Record<string, string> = {
  pricing_change: "价格变化",
  product_release: "产品发布",
  risk_experience: "风险体验"
};
const sourceNames: Record<string, string> = {
  official_page: "官方网站",
  official_changelog: "官方更新",
  pricing: "定价页面",
  product_docs: "产品文档",
  status_page: "状态页面",
  github_release: "GitHub Release",
  github_issue: "GitHub Issue",
  plugin_marketplace: "插件市场",
  community: "社区",
  review: "评测",
  security_privacy: "安全与隐私",
  benchmark: "Benchmark",
  rss: "RSS"
};
const filterNames: Record<string, string> = {
  competitor: "竞品",
  event_types: "事件",
  dimension_tags: "能力",
  product_versions: "版本",
  start_time: "起始",
  end_time: "截止",
  evidence_levels: "证据",
  source_types: "来源",
  current_only: "当前版本"
};

const indexedChunks = computed(() => store.ready?.indexed_chunks ?? 0);
const evidence = computed(() => response.value?.evidence ?? []);
const latency = computed(() => response.value ? Math.round(response.value.retrieval_trace.latency_ms) : null);
function shortDate(value?: string | null) {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("zh-CN");
}

function displayFilterValue(key: string, value: unknown) {
  if (key === "dimension_tags" && typeof value === "string") return dimensionNames[value as Dimension] || value;
  if (key === "event_types" && typeof value === "string") return eventNames[value] || value;
  if (key === "source_types" && typeof value === "string") return sourceNames[value] || value;
  if ((key === "start_time" || key === "end_time") && typeof value === "string") return shortDate(value);
  if (key === "current_only" && value === true) return "仅当前有效";
  return String(value);
}

const parsedFilterTags = computed(() => {
  if (!response.value) return [];
  return Object.entries(response.value.parsed_filters).flatMap(([key, raw]) => {
    const values = Array.isArray(raw) ? raw : [raw];
    return values
      .filter((value) => value !== null && value !== undefined && value !== "" && value !== false)
      .map((value) => ({
        key: `${key}-${String(value)}`,
        label: filterNames[key] || key,
        value: displayFilterValue(key, value)
      }));
  });
});
const parsedPrimaryFilters = computed(() => parsedFilterTags.value.filter((item) => item.label !== "起始" && item.label !== "截止"));
const parsedTimeFilters = computed(() => parsedFilterTags.value.filter((item) => item.label === "起始" || item.label === "截止"));

function historyRecord(item: EvidenceHistorySummary | EvidenceHistoryDetail) {
  return item as unknown as Record<string, unknown>;
}

function historyText(item: EvidenceHistorySummary | EvidenceHistoryDetail, key: string, fallback = "") {
  const value = historyRecord(item)[key];
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
    history.value = await ragApi.history({ page, page_size: HISTORY_PAGE_SIZE });
  } catch (reason) {
    historyError.value = errorMessage(reason);
  } finally {
    historyLoading.value = false;
  }
}

function extractResponse(detail: EvidenceHistoryDetail): RAGResponse {
  const record = historyRecord(detail);
  const embedded = record.response;
  return (embedded && typeof embedded === "object" ? embedded : detail) as RAGResponse;
}

async function restoreHistory(item: EvidenceHistorySummary) {
  const queryId = historyText(item, "query_id");
  if (!queryId || restoringId.value) return;
  restoringId.value = queryId;
  try {
    const detail = await ragApi.detail(queryId);
    const restored = extractResponse(detail);
    form.question = detail.request.question || restored.query;
    form.competitor = detail.request.competitor || "";
    form.dimensions = detail.request.dimension_tags || [];
    response.value = restored;
    status.value = "success";
    activeTab.value = "current";
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

function resultDimensions(item: EvidenceReference) {
  return item.dimension_tags.map((dimension) => dimensionNames[dimension] || dimension);
}

function buildRequest(question: string): RAGQueryRequest {
  const payload: RAGQueryRequest = { question, top_k: 10 };
  if (form.competitor) payload.competitor = form.competitor;
  const dimensions = form.dimensions.filter((dimension): dimension is Dimension => dimension !== "__all__");
  if (dimensions.length) payload.dimension_tags = dimensions;
  if (form.timePreset !== "all") Object.assign(payload, buildAnalysisWindow(form.timePreset));
  return payload;
}

function handleDimensionChange(values: DimensionSelection[]) {
  if (values.includes("__all__")) form.dimensions = [];
}

async function search(questionOverride?: string) {
  const question = (questionOverride ?? form.question).trim();
  if (!question || status.value === "searching") return;
  form.question = question;
  status.value = "searching";
  error.value = "";
  response.value = null;
  try {
    response.value = await ragApi.query(buildRequest(question));
    status.value = "success";
    if (history.value) void loadHistory(1);
  } catch (reason) {
    error.value = errorMessage(reason);
    status.value = "failed";
  }
}

function handleEnter(event: KeyboardEvent) {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  void search();
}

function useExample(example: ExampleQuery) {
  if (status.value === "searching") return;
  form.question = example.question;
  form.competitor = example.competitor;
  form.timePreset = example.timePreset;
  form.dimensions = [...example.dimensions];
  void search(example.question);
}

function openEvidence(item: EvidenceReference) {
  selectedEvidence.value = item;
  copied.value = false;
  drawerOpen.value = true;
}

async function copyChunkId() {
  const value = selectedEvidence.value?.chunk_id;
  if (!value) return;
  await navigator.clipboard.writeText(value);
  copied.value = true;
}

onMounted(async () => {
  if (!store.ready) await store.refreshStatus();
  if (!store.competitors.length) await store.loadCompetitors();
});
</script>

<template>
  <div class="evidence-search-page route-grid">
    <div class="evidence-search__inner">
      <header class="evidence-search__intro primary-page-intro">
        <p class="eyebrow">MINI-RAG / EVIDENCE RETRIEVAL</p>
        <h1>证据检索</h1>
      <p>用自然语言搜索索引中的原始证据片段。快速查看来源、上下文与关键细节。</p>
        <nav class="history-tabs history-tabs--page" aria-label="证据检索内容">
          <button type="button" :class="{ active: activeTab === 'current' }" @click="switchTab('current')">当前检索</button>
          <button type="button" :class="{ active: activeTab === 'history' }" @click="switchTab('history')">检索历史</button>
        </nav>
      </header>

      <section v-if="activeTab === 'history'" class="evidence-history" aria-label="检索历史">
        <div class="evidence-history__heading">
          <div><span class="section-index">01</span><h2>检索历史</h2></div>
          <button type="button" :disabled="historyLoading" @click="loadHistory(1)">刷新</button>
        </div>
        <div v-if="historyLoading" class="evidence-history__state">正在读取历史记录…</div>
        <div v-else-if="historyError" class="evidence-history__state evidence-history__state--error">{{ historyError }}</div>
        <div v-else-if="history?.items.length" class="evidence-history__list">
          <button
            v-for="item in history.items"
            :key="item.query_id"
            type="button"
            :disabled="Boolean(restoringId)"
            @click="restoreHistory(item)"
          >
            <span class="mono">{{ formatHistoryDate(item.created_at) }}</span>
            <strong>{{ item.question || '未命名检索' }}</strong>
            <small>{{ item.competitor || '全部竞品' }} · {{ item.result_count }} 条证据</small>
            <em v-if="restoringId === item.query_id">恢复中…</em>
          </button>
        </div>
        <div v-else class="evidence-history__state">暂时还没有检索记录。</div>
        <el-pagination
          v-if="history && history.total_pages > 1"
          class="history-pagination"
          background
          layout="prev, pager, next"
          :current-page="history.page"
          :page-count="history.total_pages"
          @current-change="loadHistory"
        />
      </section>

      <template v-else>
      <section class="evidence-console" aria-label="证据检索条件">
        <div class="evidence-query">
          <i class="evidence-query__corner evidence-query__corner--tl" aria-hidden="true" />
          <i class="evidence-query__corner evidence-query__corner--tr" aria-hidden="true" />
          <i class="evidence-query__corner evidence-query__corner--bl" aria-hidden="true" />
          <i class="evidence-query__corner evidence-query__corner--br" aria-hidden="true" />
          <span class="evidence-query__index">QUERY / 01</span>
          <el-input
            v-model="form.question"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            maxlength="2000"
            resize="none"
            placeholder="例如：Cursor 最近 30 天有哪些 Agent 上下文更新？"
            @keydown="handleEnter"
          />
          <button
            class="evidence-query__submit"
            type="button"
            :disabled="!form.question.trim() || status === 'searching'"
            @click="search()"
          >
            <span>{{ status === "searching" ? "检索中" : "开始检索" }}</span>
            <b aria-hidden="true">↗</b>
          </button>
        </div>

        <div class="evidence-filters">
          <label>
            <span>竞品</span>
            <el-select v-model="form.competitor" clearable placeholder="全部竞品">
              <el-option label="全部竞品" value="" />
              <el-option
                v-for="item in store.enabledCompetitors"
                :key="item.id"
                :label="item.name"
                :value="item.name"
              />
            </el-select>
          </label>
          <label>
            <span>时间范围</span>
            <el-select v-model="form.timePreset">
              <el-option
                v-for="item in ANALYSIS_TIME_OPTIONS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
          </label>
          <label>
            <span>能力维度</span>
            <el-select
              v-model="form.dimensions"
              multiple
              collapse-tags
              collapse-tags-tooltip
              clearable
              placeholder="全部维度"
              @change="handleDimensionChange"
            >
              <el-option label="全部维度" value="__all__" />
              <el-option
                v-for="item in DIMENSIONS"
                :key="item.key"
                :label="`${item.code} · ${item.name}`"
                :value="item.key"
              />
            </el-select>
          </label>
        </div>

        <div class="evidence-examples" aria-label="检索灵感">
          <div class="evidence-examples__heading">
            <span>检索灵感</span>
            <small>VERIFIED QUERIES</small>
          </div>
          <div class="evidence-examples__list">
            <button
              v-for="(item, index) in examples"
              :key="item.id"
              type="button"
              :disabled="status === 'searching'"
              @click="useExample(item)"
            >
              <small>{{ String(index + 1).padStart(2, "0") }} / {{ item.competitor }}</small>
              <strong>{{ item.question }}</strong>
              <span>{{ item.dimensions.map((dimension) => dimensionNames[dimension]).join(" · ") }} <i>↗</i></span>
            </button>
          </div>
        </div>
      </section>

      <section v-if="status !== 'ready'" class="evidence-intelligence" aria-label="系统识别与检索耗时">
        <div class="evidence-intelligence__parsed">
          <span>系统识别</span>
          <div v-if="response && parsedFilterTags.length">
            <p>
              <template v-for="(item, index) in parsedPrimaryFilters" :key="item.key">
                <i v-if="index" aria-hidden="true">/</i>
                <strong>{{ item.value }}</strong>
              </template>
            </p>
            <small v-if="parsedTimeFilters.length">
              {{ parsedTimeFilters.map((item) => `${item.label} ${item.value}`).join(" — ") }}
            </small>
          </div>
          <div v-else>
            <p>{{ status === "searching" ? "正在识别检索意图…" : "未能完成条件识别" }}</p>
            <small>{{ status === "searching" ? "PARSING QUERY AND FILTERS" : "QUERY PARSING UNAVAILABLE" }}</small>
          </div>
        </div>
        <div class="evidence-intelligence__latency">
          <span>检索耗时</span>
          <strong>{{ latency === null ? "—" : latency }}</strong>
          <small>{{ latency === null ? "LATENCY" : "MILLISECONDS" }}</small>
        </div>
      </section>

      <section class="evidence-results">
        <div class="section-heading">
          <div>
            <span class="section-index">02</span>
            <h2>检索结果</h2>
            <p>按 Mini-RAG 综合相关度排列，点击任意一行查看完整片段与溯源信息</p>
          </div>
          <span v-if="response" class="evidence-results__query mono">{{ response.query_id }}</span>
        </div>

        <div v-if="response?.conflicts.length" class="evidence-conflict">
          <strong>检测到 {{ response.conflicts.length }} 组证据冲突</strong>
          <span>结果保留全部相关证据，请结合发布时间与证据等级判断。</span>
        </div>

        <div v-if="status === 'searching'" class="evidence-loading" aria-label="正在检索">
          <div v-for="index in 3" :key="index"><i /><span><b /><b /></span><em /></div>
        </div>
        <div v-else-if="status === 'failed'" class="evidence-state evidence-state--error">
          <span>QUERY FAILED</span>
          <h3>这次检索没有完成</h3>
          <p>{{ error }}</p>
          <button type="button" @click="search()">重新检索 ↗</button>
        </div>
        <div v-else-if="response && !evidence.length" class="evidence-state">
          <span>NO MATCHES</span>
          <h3>没有找到符合条件的证据</h3>
          <p>可以缩短问题、清空部分筛选条件，或换一种表达方式。</p>
        </div>
        <div v-else-if="!response" class="evidence-state evidence-state--idle">
          <span>WAITING FOR QUERY</span>
          <h3>{{ indexedChunks || "全部" }} 个证据坐标正在等待检索</h3>
          <p>输入自然语言问题，系统会直接返回最相关的原始片段。</p>
        </div>
        <div v-else class="evidence-list">
          <article
            v-for="(item, index) in evidence"
            :key="item.chunk_id"
            tabindex="0"
            role="button"
            :aria-label="`查看证据：${item.title}`"
            @click="openEvidence(item)"
            @keydown.enter="openEvidence(item)"
          >
            <div class="evidence-list__index">{{ String(index + 1).padStart(2, "0") }}</div>
            <div class="evidence-list__body">
              <span>{{ item.competitor }} · {{ sourceNames[item.source_type] || item.source_type }}</span>
              <h3>{{ item.title }}</h3>
              <p>{{ item.quote || item.content || "暂无证据正文" }}</p>
            </div>
            <div class="evidence-list__meta">
              <div v-if="resultDimensions(item).length" class="evidence-list__meta-tags">
                <small v-for="dimension in resultDimensions(item).slice(0, 3)" :key="dimension">{{ dimension }}</small>
                <small v-if="resultDimensions(item).length > 3">+{{ resultDimensions(item).length - 3 }}</small>
              </div>
              <span>{{ shortDate(item.publish_time) }}</span>
            </div>
          </article>
        </div>
      </section>
      </template>
    </div>

    <el-drawer
      v-model="drawerOpen"
      class="evidence-detail-drawer"
      size="620px"
      title="证据详情"
      destroy-on-close
    >
      <article v-if="selectedEvidence" class="evidence-detail">
        <p class="eyebrow">SOURCE-LOCATABLE CHUNK</p>
        <h2>{{ selectedEvidence.title }}</h2>
        <div class="evidence-detail__rail">
          <span>{{ selectedEvidence.competitor }}</span>
          <span>{{ selectedEvidence.evidence_level }} 级证据</span>
          <span>{{ sourceNames[selectedEvidence.source_type] || selectedEvidence.source_type }}</span>
          <span>{{ shortDate(selectedEvidence.publish_time) }}</span>
        </div>
        <div v-if="selectedEvidence.heading_path?.length" class="evidence-detail__path">
          {{ selectedEvidence.heading_path.join(" / ") }}
        </div>
        <section>
          <span>证据正文</span>
          <p>{{ selectedEvidence.content || selectedEvidence.quote || "暂无证据正文" }}</p>
        </section>
        <dl>
          <div><dt>能力维度</dt><dd>{{ resultDimensions(selectedEvidence).join("、") || "未标注" }}</dd></div>
          <div><dt>当前有效</dt><dd>{{ selectedEvidence.is_current === false ? "否" : "是" }}</dd></div>
          <div><dt>检索方式</dt><dd>{{ selectedEvidence.retrieval_methods?.join(" + ") || "未提供" }}</dd></div>
          <div><dt>最终分数</dt><dd>{{ selectedEvidence.final_score == null ? "未提供" : selectedEvidence.final_score.toFixed(4) }}</dd></div>
          <div><dt>Chunk ID</dt><dd class="mono">{{ selectedEvidence.chunk_id }}</dd></div>
          <div><dt>Document ID</dt><dd class="mono">{{ selectedEvidence.document_id }}</dd></div>
          <div><dt>Version ID</dt><dd class="mono">{{ selectedEvidence.version_id }}</dd></div>
        </dl>
        <div class="evidence-detail__actions">
          <button type="button" @click="copyChunkId">{{ copied ? "已复制" : "复制 Chunk ID" }}</button>
          <a :href="selectedEvidence.url" target="_blank" rel="noopener noreferrer">打开原始来源 ↗</a>
        </div>
      </article>
    </el-drawer>
  </div>
</template>
