<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { Refresh, UploadFilled } from "@element-plus/icons-vue";
import { adminApi } from "../api/services";
import { ApiError, errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import {
  DIMENSIONS,
  type AdminImportResponse,
  type AdminOverview,
  type Dimension
} from "../types/api";

type ImportPhase =
  | "idle"
  | "validating"
  | "uploading"
  | "saving"
  | "indexing"
  | "success"
  | "partial_failure"
  | "failed";

const MAX_FILE_SIZE = 5 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".md", ".txt", ".jsonl"];
const REFRESH_INTERVAL = 30_000;

const store = useAppStore();
const overview = ref<AdminOverview | null>(null);
const overviewLoading = ref(false);
const overviewError = ref("");
const refreshedAt = ref<Date | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const selectedFile = ref<File | null>(null);
const dragging = ref(false);
const importPhase = ref<ImportPhase>("idle");
const uploadProgress = ref(0);
const importError = ref("");
const importResult = ref<AdminImportResponse | null>(null);
let refreshTimer: ReturnType<typeof setInterval> | undefined;
let phaseTimer: ReturnType<typeof setTimeout> | undefined;

const form = reactive({
  competitor: "",
  title: "",
  sourceUrl: "",
  publishTime: new Date().toISOString().slice(0, 10),
  dimensions: [] as Dimension[]
});

const documents = computed(() => overview.value?.stats ?? {
  documents_total: 0,
  current_versions: 0,
  historical_versions: 0,
  pending_review: 0
});
const competitorDistribution = computed(() =>
  Object.entries(overview.value?.distributions.competitors ?? {}).map(([name, count]) => ({ name, count }))
);
const sourceDistribution = computed(() =>
  Object.entries(overview.value?.distributions.source_types ?? {}).map(([name, count]) => ({ name, count }))
);
const maxCompetitorCount = computed(() => Math.max(1, ...competitorDistribution.value.map((item) => item.count)));
const maxSourceCount = computed(() => Math.max(1, ...sourceDistribution.value.map((item) => item.count)));
const isJsonl = computed(() => selectedFile.value?.name.toLowerCase().endsWith(".jsonl") ?? false);
const isImporting = computed(() => ["validating", "uploading", "saving", "indexing"].includes(importPhase.value));
const status = computed(() => store.ready);
const backendState = computed(() => String(status.value?.backend.status || "unknown").toLowerCase());
const systemHealthy = computed(() =>
  store.apiOnline &&
  status.value?.status === "ready" &&
  ["green", "yellow"].includes(backendState.value) &&
  status.value.embedding_compatible
);
const phaseIndex = computed(() => {
  if (importPhase.value === "validating") return 0;
  if (importPhase.value === "uploading") return 1;
  if (importPhase.value === "saving") return 2;
  if (["indexing", "success", "partial_failure"].includes(importPhase.value)) return 3;
  return -1;
});

const serviceChecks = computed(() => [
  {
    label: "API 服务",
    detail: store.apiOnline ? "HTTP 接口响应正常" : "无法连接服务",
    state: store.apiOnline ? "normal" : "failed"
  },
  {
    label: "Mini-RAG",
    detail: status.value?.status === "ready" ? "检索服务已就绪" : "检索服务未就绪",
    state: status.value?.status === "ready" ? "normal" : "failed"
  },
  {
    label: "Elasticsearch",
    detail: `${backendState.value.toUpperCase()} · ${status.value?.backend.number_of_nodes ?? "—"} 节点`,
    state: backendState.value === "green" ? "normal" : backendState.value === "yellow" ? "warning" : "failed"
  },
  {
    label: "向量模型",
    detail: status.value?.embedding_compatible ? "模型与索引兼容" : "模型与索引不兼容",
    state: status.value?.embedding_compatible ? "normal" : "failed"
  },
  {
    label: "索引证据",
    detail: `${formatNumber(status.value?.indexed_chunks)} chunks`,
    state: status.value?.indexed_chunks !== null && status.value?.indexed_chunks !== undefined ? "normal" : "warning"
  }
]);

function formatNumber(value?: number | null) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "—";
}

function formatDate(value?: string | null, withTime = false) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", withTime
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" }
  ).format(parsed);
}

function sourceLabel(value: string) {
  const labels: Record<string, string> = {
    product_docs: "产品文档",
    official_page: "官方网站",
    official_changelog: "官方更新",
    github_release: "GitHub Release",
    community: "社区",
    review: "评测",
    security_privacy: "安全与隐私"
  };
  return labels[value] || value;
}

async function refreshAll() {
  overviewLoading.value = true;
  overviewError.value = "";
  const tasks: Promise<unknown>[] = [
    store.refreshStatus(),
    adminApi.overview()
      .then((data) => {
        overview.value = data;
      })
      .catch((reason) => {
        overviewError.value = errorMessage(reason);
      })
  ];
  if (!store.competitors.length) {
    tasks.push(store.loadCompetitors().catch(() => undefined));
  }
  await Promise.all(tasks);
  refreshedAt.value = new Date();
  overviewLoading.value = false;
}

function chooseFile() {
  if (!isImporting.value) fileInput.value?.click();
}

function validateFile(file: File): string {
  const lowerName = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
    return "格式不支持，请选择 Markdown、TXT 或 JSONL 文件";
  }
  if (file.size > MAX_FILE_SIZE) return "文件超过 5 MB 限制";
  if (file.size === 0) return "文件内容为空";
  return "";
}

function selectFile(file?: File) {
  if (!file || isImporting.value) return;
  const validationError = validateFile(file);
  importResult.value = null;
  uploadProgress.value = 0;
  if (validationError) {
    selectedFile.value = null;
    importPhase.value = "failed";
    importError.value = validationError;
    return;
  }
  selectedFile.value = file;
  importPhase.value = "idle";
  importError.value = "";
  if (!file.name.toLowerCase().endsWith(".jsonl") && !form.title) {
    form.title = file.name.replace(/\.(md|txt)$/i, "");
  }
}

function onFileChange(event: Event) {
  selectFile((event.target as HTMLInputElement).files?.[0]);
  (event.target as HTMLInputElement).value = "";
}

function onDrop(event: DragEvent) {
  dragging.value = false;
  selectFile(event.dataTransfer?.files?.[0]);
}

function clearFile() {
  if (isImporting.value) return;
  selectedFile.value = null;
  importPhase.value = "idle";
  importError.value = "";
  importResult.value = null;
  uploadProgress.value = 0;
}

function validateMetadata() {
  if (!selectedFile.value) return "请先选择需要导入的文件";
  const fileError = validateFile(selectedFile.value);
  if (fileError) return fileError;
  if (!isJsonl.value && !form.competitor) return "请选择文档所属竞品";
  if (!isJsonl.value && !form.title.trim()) return "请填写文档标题";
  if (form.sourceUrl) {
    try {
      new URL(form.sourceUrl);
    } catch {
      return "来源网址格式不正确";
    }
  }
  return "";
}

async function submitImport() {
  if (isImporting.value) return;
  importPhase.value = "validating";
  importError.value = "";
  importResult.value = null;
  uploadProgress.value = 0;
  const validationError = validateMetadata();
  if (validationError) {
    importPhase.value = "failed";
    importError.value = validationError;
    return;
  }

  importPhase.value = "uploading";
  try {
    const result = await adminApi.importDocuments(
      selectedFile.value!,
      isJsonl.value ? {} : {
        competitor: form.competitor,
        title: form.title.trim(),
        source_url: form.sourceUrl.trim() || undefined,
        publish_time: form.publishTime || undefined,
        dimension_tags: form.dimensions,
        source_type: "product_docs",
        evidence_level: "C"
      },
      (percent) => {
        uploadProgress.value = percent;
        if (percent === 100) {
          importPhase.value = "saving";
          if (phaseTimer) clearTimeout(phaseTimer);
          phaseTimer = setTimeout(() => {
            if (importPhase.value === "saving") importPhase.value = "indexing";
          }, 450);
        }
      }
    );
    importResult.value = result;
    uploadProgress.value = 100;
    importPhase.value = result.status === "save_failed" ? "failed" : result.status;
    await refreshAll();
  } catch (reason) {
    importPhase.value = "failed";
    const response = reason instanceof ApiError
      ? reason.problem as unknown as Partial<AdminImportResponse> | undefined
      : undefined;
    if (response?.status === "save_failed") {
      importResult.value = response as AdminImportResponse;
      importError.value = response.errors?.join("；") || "文档保存失败";
    } else {
      importError.value = errorMessage(reason);
    }
  }
}

onMounted(() => {
  void refreshAll();
  refreshTimer = setInterval(() => void refreshAll(), REFRESH_INTERVAL);
});

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer);
  if (phaseTimer) clearTimeout(phaseTimer);
});
</script>

<template>
  <div class="admin-page route-grid">
    <div class="admin-page__inner">
      <header class="admin-intro primary-page-intro">
        <div>
          <p class="eyebrow">SYSTEM OPERATIONS / LIVE CONSOLE</p>
          <h1>系统管理</h1>
          <p>查看接口、索引与服务运行状态，导入新的讯息。</p>
        </div>
        <div class="admin-refresh">
          <span :class="{ healthy: systemHealthy }"><i />{{ systemHealthy ? "系统运行正常" : "存在待检查项" }}</span>
          <small>最后刷新 {{ refreshedAt ? refreshedAt.toLocaleTimeString("zh-CN", { hour12: false }) : "—" }} · 每 30 秒自动更新</small>
          <el-button :icon="Refresh" :loading="overviewLoading" round @click="refreshAll">刷新状态</el-button>
        </div>
      </header>

      <section class="admin-service-grid" aria-label="服务运行状态">
        <article v-for="(item, index) in serviceChecks" :key="item.label" :data-state="item.state">
          <span class="admin-service-grid__index">0{{ index + 1 }}</span>
          <i class="admin-service-grid__signal" />
          <div>
            <h2>{{ item.label }}</h2>
            <p>{{ item.detail }}</p>
          </div>
        </article>
      </section>

      <p v-if="overviewError" class="admin-error-note">
        内容统计暂时不可用：{{ overviewError }}。服务运行状态仍可单独刷新。
      </p>

      <section class="admin-section admin-feature-section" aria-labelledby="admin-content-heading">
        <div class="admin-section__heading">
          <div>
            <span>01</span>
            <div>
              <p>CONTENT OVERVIEW</p>
              <h2 id="admin-content-heading">内容概览</h2>
            </div>
          </div>
          <div class="admin-section__actions">
            <small>数据集更新于 {{ formatDate(overview?.dataset_updated_at, true) }}</small>
            <router-link class="admin-documents-cta" to="/admin/documents">
              查看全部文档 <span aria-hidden="true">↗</span>
            </router-link>
          </div>
        </div>

        <div class="admin-content-grid">
          <div class="admin-metrics">
            <router-link to="/admin/documents" aria-label="查看全部文档">
              <span>文档总量</span><strong>{{ formatNumber(documents.documents_total) }}</strong><small>DOCUMENTS</small><i>查看全部 →</i>
            </router-link>
            <router-link :to="{ path: '/admin/documents', query: { status: 'current' } }" aria-label="查看当前版本文档">
              <span>当前版本</span><strong>{{ formatNumber(documents.current_versions) }}</strong><small>CURRENT</small><i>查看列表 →</i>
            </router-link>
            <router-link :to="{ path: '/admin/documents', query: { status: 'historical' } }" aria-label="查看历史版本文档">
              <span>历史版本</span><strong>{{ formatNumber(documents.historical_versions) }}</strong><small>HISTORICAL</small><i>查看列表 →</i>
            </router-link>
            <router-link :to="{ path: '/admin/documents', query: { status: 'review' } }" :class="{ attention: documents.pending_review > 0 }" aria-label="查看待审核文档">
              <span>待审核</span><strong>{{ formatNumber(documents.pending_review) }}</strong><small>REVIEW QUEUE</small><i>查看列表 →</i>
            </router-link>
          </div>

          <div class="admin-distribution">
            <div class="admin-subheading"><span>竞品覆盖</span><small>按文档数量</small></div>
            <div v-if="competitorDistribution.length" class="distribution-list">
              <div v-for="item in competitorDistribution.slice(0, 5)" :key="item.name">
                <span>{{ item.name }}</span>
                <i><b :style="{ width: `${Math.max(4, item.count / maxCompetitorCount * 100)}%` }" /></i>
                <strong>{{ item.count }}</strong>
              </div>
            </div>
            <p v-else class="admin-empty">暂无分布数据</p>
            <div class="admin-subheading admin-subheading--secondary"><span>来源构成</span><small>按文档数量</small></div>
            <div v-if="sourceDistribution.length" class="distribution-list">
              <div v-for="item in sourceDistribution.slice(0, 5)" :key="item.name">
                <span>{{ sourceLabel(item.name) }}</span>
                <i><b :style="{ width: `${Math.max(4, item.count / maxSourceCount * 100)}%` }" /></i>
                <strong>{{ item.count }}</strong>
              </div>
            </div>
            <p v-else class="admin-empty">暂无来源数据</p>
          </div>
        </div>

      </section>

      <section class="admin-section admin-operations-section" aria-labelledby="admin-operations-heading">
        <div class="admin-section__heading">
          <div>
            <span>02</span>
            <div><p>CONFIGURATION / TELEMETRY</p><h2 id="admin-operations-heading">配置与监控</h2></div>
          </div>
          <small>只读配置 · /ready 实时状态</small>
        </div>

        <div class="operations-grid">
          <div class="operations-classification">
            <div class="admin-subheading"><span>竞品对象</span><small>{{ store.competitors.length }} 个配置</small></div>
            <ul class="competitor-list">
              <li v-for="competitor in store.competitors" :key="competitor.id">
                <i :class="{ enabled: competitor.enabled }" />
                <div><strong>{{ competitor.name }}</strong><small>{{ competitor.aliases.join(" / ") || "暂无别名" }}</small></div>
                <span>{{ competitor.enabled ? "启用" : "停用" }}</span>
              </li>
            </ul>
            <div class="admin-subheading admin-subheading--dimensions"><span>能力维度</span><small>D1 — D7</small></div>
            <ol class="dimension-list">
              <li v-for="dimension in DIMENSIONS" :key="dimension.key">
                <span>{{ dimension.code }}</span><strong>{{ dimension.name }}</strong><small>{{ dimension.key }}</small>
              </li>
            </ol>
          </div>
          <div class="operations-monitor">
            <div class="admin-subheading"><span>系统监控</span><small>INDEX TELEMETRY</small></div>
            <dl class="monitor-grid">
              <div><dt>逻辑索引</dt><dd>{{ status?.index || "—" }}</dd></div>
              <div><dt>物理索引</dt><dd>{{ status?.physical_index || "—" }}</dd></div>
              <div><dt>Embedding 模型</dt><dd>{{ status?.embedding_model || status?.index_embedding_model || "—" }}</dd></div>
              <div><dt>向量维度</dt><dd>{{ status?.embedding_dimension || status?.index_embedding_dimension || "—" }}</dd></div>
              <div><dt>活动分片</dt><dd>{{ formatNumber(status?.backend.active_shards as number | undefined) }}</dd></div>
              <div><dt>未分配分片</dt><dd>{{ formatNumber(status?.backend.unassigned_shards as number | undefined) }}</dd></div>
            </dl>
          </div>
        </div>
      </section>

      <section class="admin-section admin-feature-section admin-import-section" aria-labelledby="admin-import-heading">
        <div class="admin-section__heading">
          <div>
            <span>03</span>
            <div><p>MANUAL INGESTION</p><h2 id="admin-import-heading">导入新文档</h2></div>
          </div>
          <small>UTF-8 · MD / TXT / JSONL · 最大 5 MB</small>
        </div>

        <div class="import-layout">
          <div>
            <input ref="fileInput" class="visually-hidden" type="file" accept=".md,.txt,.jsonl,text/plain,text/markdown,application/jsonl" @change="onFileChange">
            <button
              type="button"
              class="drop-zone"
              :class="{ dragging, selected: selectedFile }"
              :disabled="isImporting"
              @click="chooseFile"
              @dragenter.prevent="dragging = true"
              @dragover.prevent="dragging = true"
              @dragleave.prevent="dragging = false"
              @drop.prevent="onDrop"
            >
              <el-icon><UploadFilled /></el-icon>
              <template v-if="selectedFile">
                <strong>{{ selectedFile.name }}</strong>
                <span>{{ (selectedFile.size / 1024).toFixed(1) }} KB · {{ isJsonl ? "标准 JSONL" : "文本资料" }}</span>
                <small>点击或拖入其他文件进行替换</small>
              </template>
              <template v-else>
                <strong>拖入资料，或点击选择文件</strong>
                <span>支持 Markdown、纯文本和标准文档 JSONL</span>
                <small>单个文件不超过 5 MB</small>
              </template>
            </button>
            <button v-if="selectedFile && !isImporting" type="button" class="remove-file" @click="clearFile">移除当前文件</button>

            <div class="import-steps" :data-phase="importPhase">
              <div v-for="(label, index) in ['校验', '上传', '保存', '索引']" :key="label" :class="{ active: phaseIndex === index, done: phaseIndex > index || ['success', 'partial_failure'].includes(importPhase) }">
                <i>{{ phaseIndex > index || ['success', 'partial_failure'].includes(importPhase) ? "✓" : index + 1 }}</i>
                <span>{{ label }}</span>
              </div>
            </div>
            <div v-if="isImporting" class="import-progress">
              <i><b :style="{ width: `${importPhase === 'uploading' ? uploadProgress : 100}%` }" /></i>
              <span>{{ importPhase === "uploading" ? `${uploadProgress}%` : importPhase === "saving" ? "正在安全写入数据集…" : "正在同步向量索引…" }}</span>
            </div>
          </div>

          <div class="import-form">
            <template v-if="!isJsonl">
              <label><span>所属竞品 *</span><el-select v-model="form.competitor" placeholder="选择竞品" filterable><el-option v-for="item in store.enabledCompetitors" :key="item.id" :label="item.name" :value="item.name" /></el-select></label>
              <label><span>文档标题 *</span><el-input v-model="form.title" maxlength="200" placeholder="例如：Cursor Agent 产品更新" /></label>
              <div class="import-form__row">
                <label><span>发布时间</span><el-date-picker v-model="form.publishTime" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" /></label>
                <label><span>来源网址</span><el-input v-model="form.sourceUrl" placeholder="https://…" /></label>
              </div>
              <label><span>能力标签（可选）</span><el-select v-model="form.dimensions" multiple collapse-tags placeholder="选择 D1–D7"><el-option v-for="item in DIMENSIONS" :key="item.key" :label="`${item.code} · ${item.name}`" :value="item.key" /></el-select></label>
              <p class="form-defaults">来源类型固定为 product_docs · 证据等级默认为 C</p>
            </template>
            <div v-else class="jsonl-notice">
              <span>JSONL STANDARD MODE</span>
              <h3>读取文件内的标准字段</h3>
              <p>竞品、标题、来源、发布时间和能力标签均使用每条记录自身的数据，不应用右侧表单元数据。</p>
            </div>

            <button type="button" class="import-submit" :disabled="!selectedFile || isImporting" @click="submitImport">
              <span>{{ isImporting ? "正在导入" : "开始导入并索引" }}</span><i>→</i>
            </button>
          </div>
        </div>

        <div v-if="importPhase === 'failed'" class="import-result failed" role="alert">
          <strong>导入未完成</strong><p>{{ importError || importResult?.errors.join("；") || "请检查文件后重试" }}</p>
        </div>
        <div v-else-if="importResult" class="import-result" :class="{ partial: importPhase === 'partial_failure' }">
          <div><strong>{{ importPhase === "success" ? "文档已导入并完成索引" : "文档已保存，索引需要重试" }}</strong><p>{{ importResult.warnings.join("；") || (importResult.persisted ? "数据集已安全更新" : "数据集未发生变化") }}</p></div>
          <dl>
            <div><dt>导入文档</dt><dd>{{ importResult.documents_imported }}</dd></div>
            <div><dt>跳过文档</dt><dd>{{ importResult.documents_skipped }}</dd></div>
            <div><dt>生成 Chunk</dt><dd>{{ importResult.chunks_generated }}</dd></div>
            <div><dt>索引 Chunk</dt><dd>{{ importResult.chunks_indexed }}</dd></div>
            <div><dt>索引总量</dt><dd>{{ formatNumber(importResult.indexed_chunks_total) }}</dd></div>
          </dl>
          <p v-if="importResult.errors.length" class="import-result__errors">{{ importResult.errors.join("；") }}</p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-page { padding-bottom: 96px; }
.admin-page__inner { width: min(100%, 1280px); margin: 0 auto; }
.admin-intro { min-height: 0; display: flex; justify-content: space-between; align-items: flex-end; gap: 48px; padding: 58px 0 42px; border-bottom: 1px solid rgba(25,31,42,.28); }
.admin-intro h1 { margin: 13px 0 10px; font-size: clamp(34px,4vw,54px); font-weight: 500; line-height: 1.12; letter-spacing: -.025em; }
.admin-intro > div:first-child > p:last-child { max-width: 620px; margin: 0; color: var(--muted); font-size: 16px; line-height: 1.75; }
.admin-refresh { min-width: 290px; display: grid; justify-items: end; gap: 9px; text-align: right; }
.admin-refresh > span { display: flex; align-items: center; gap: 9px; font-size: 13px; }
.admin-refresh > span i { width: 7px; height: 7px; border-radius: 50%; background: #d66b62; box-shadow: 0 0 0 4px rgba(214,107,98,.1); }
.admin-refresh > span.healthy i { background: #2ab877; box-shadow: 0 0 0 4px rgba(42,184,119,.1), 0 0 14px rgba(42,184,119,.4); }
.admin-refresh small { color: var(--muted); font-family: ui-monospace,monospace; font-size: 10px; letter-spacing: .05em; }
.admin-refresh .el-button { margin-top: 4px; background: rgba(251,250,246,.5); }
.admin-service-grid { display: grid; grid-template-columns: repeat(5,1fr); border-bottom: 1px solid rgba(25,31,42,.28); }
.admin-service-grid article { position: relative; min-height: 155px; padding: 25px 22px; border-right: 1px solid rgba(25,31,42,.2); background: rgba(251,250,246,.3); }
.admin-service-grid article:last-child { border-right: 0; }
.admin-service-grid__index { color: rgba(21,23,27,.42); font-family: ui-monospace,monospace; font-size: 10px; }
.admin-service-grid__signal { position: absolute; top: 26px; right: 22px; width: 8px; height: 8px; border-radius: 50%; background: #2ab877; box-shadow: 0 0 0 4px rgba(42,184,119,.08); }
.admin-service-grid article[data-state="warning"] .admin-service-grid__signal { background: #d8a238; }
.admin-service-grid article[data-state="failed"] .admin-service-grid__signal { background: #d66b62; }
.admin-service-grid h2 { margin: 42px 0 7px; font-size: 17px; font-weight: 500; }
.admin-service-grid p { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
.admin-error-note { margin: 20px 0 0; padding: 11px 14px; color: #8c4c1f; border-block: 1px solid rgba(174,103,46,.25); background: rgba(208,146,70,.07); font-size: 12px; }
.admin-section { padding: 72px 0 76px; border-bottom: 1px solid rgba(25,31,42,.28); }
.admin-section__heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 34px; }
.admin-section__heading > div { display: flex; align-items: flex-start; gap: 16px; }
.admin-section__heading > div > span { position: relative; min-width: 18px; color: var(--accent); font-family: ui-monospace,monospace; font-size: 13px; }
.admin-section__heading > div > span::before { content: ""; position: absolute; top: -8px; left: 0; width: 5px; height: 5px; border: 1px solid currentColor; }
.admin-section__heading p { margin: 0 0 5px; color: var(--muted); font-family: ui-monospace,monospace; font-size: 10px; letter-spacing: .15em; }
.admin-section__heading h2 { margin: 0; font-size: 28px; font-weight: 500; }
.admin-section__heading > small { padding-top: 6px; color: var(--muted); font-size: 12px; }
.admin-section__actions { display: grid !important; justify-items: end; gap: 12px !important; }
.admin-section__actions small { color: var(--muted); font-size: 12px; }
.admin-documents-cta { min-height: 42px; display: inline-flex; align-items: center; gap: 22px; padding: 0 15px; color: var(--text); border: 1px solid color-mix(in srgb,var(--accent) 42%,var(--line)); background: rgba(251,250,246,.55); font-size: 12px; text-decoration: none; transition: color .2s,border-color .2s,background .2s; }
.admin-documents-cta span { color: var(--accent); font-size: 18px; transition: transform .2s; }
.admin-documents-cta:hover,.admin-documents-cta:focus-visible { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.admin-documents-cta:hover span,.admin-documents-cta:focus-visible span { transform: translate(2px,-2px); }
.admin-content-grid { position: relative; display: grid; grid-template-columns: 1.55fr .8fr; border-block: 1px solid var(--line); }
.admin-content-grid::before,.admin-content-grid::after,.operations-grid::before,.operations-grid::after,.import-layout::before,.import-layout::after { position: absolute; z-index: 2; color: var(--accent); background: var(--paper); font-family: ui-monospace,monospace; font-size: 15px; line-height: 1; }
.admin-content-grid::before,.operations-grid::before,.import-layout::before { content: "+"; top: -8px; left: -5px; }
.admin-content-grid::after,.operations-grid::after,.import-layout::after { content: "+"; right: -5px; bottom: -8px; }
.admin-metrics { display: grid; grid-template-columns: repeat(2,1fr); border-right: 1px solid var(--line); }
.admin-metrics a { position: relative; min-height: 150px; padding: 25px; color: inherit; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); text-decoration: none; transition: background .2s,color .2s; }
.admin-metrics a:nth-child(2n) { border-right: 0; }
.admin-metrics a:nth-last-child(-n+2) { border-bottom: 0; }
.admin-metrics a:hover { color: var(--accent); background: var(--accent-soft); }
.admin-metrics span,.admin-metrics small { display: block; color: var(--muted); }
.admin-metrics span { font-size: 12px; }
.admin-metrics strong { display: block; margin-top: 15px; font-size: 38px; font-weight: 400; line-height: 1; }
.admin-metrics small { margin-top: 17px; font-family: ui-monospace,monospace; font-size: 10px; letter-spacing: .13em; }
.admin-metrics a > i { position: absolute; right: 20px; bottom: 20px; color: var(--muted); font-style: normal; font-size: 11px; opacity: .72; transform: translateX(-3px); transition: opacity .2s,transform .2s; }
.admin-metrics a:hover > i,.admin-metrics a:focus-visible > i { opacity: 1; transform: none; }
.admin-metrics a.attention strong { color: #a96d12; }
.admin-distribution { padding: 24px 26px; }
.admin-subheading { display: flex; justify-content: space-between; align-items: baseline; padding-bottom: 13px; border-bottom: 1px solid var(--line); }
.admin-subheading > span { font-size: 14px; font-weight: 500; }
.admin-subheading > small { color: var(--muted); font-size: 10px; }
.admin-subheading--secondary { margin-top: 22px; }
.distribution-list { margin-top: 9px; }
.distribution-list > div { min-height: 36px; display: grid; grid-template-columns: 98px 1fr 32px; gap: 12px; align-items: center; font-size: 11px; }
.distribution-list > div > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.distribution-list > div > i { height: 3px; overflow: hidden; background: rgba(25,31,42,.08); }
.distribution-list b { display: block; height: 100%; background: linear-gradient(90deg,#2f8cff,#67b7ff); }
.distribution-list strong { text-align: right; font-family: ui-monospace,monospace; font-weight: 500; }
.admin-table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { padding: 12px 10px; color: var(--muted); font-size: 10px; font-weight: 400; text-align: left; }
td { padding: 17px 10px; border-top: 1px solid var(--line); font-size: 12px; }
td:first-child { width: 45%; }
td strong,td small { display: block; }
td strong { max-width: 520px; overflow: hidden; font-size: 14px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
td small { margin-top: 5px; color: var(--muted); font-family: ui-monospace,monospace; font-size: 10px; }
.version-state { padding: 4px 8px; color: #127149; border: 1px solid rgba(18,113,73,.25); border-radius: 999px; font-size: 10px; }
.version-state.historical { color: var(--muted); border-color: var(--line); }
.admin-empty { min-height: 100px; display: grid; place-items: center; margin: 0; color: var(--muted); font-size: 12px; }
.admin-operations-section { padding: 24px 0 26px; }
.admin-operations-section .admin-section__heading { margin-bottom: 12px; }
.admin-operations-section .admin-subheading { padding-bottom: 8px; }
.operations-grid { position: relative; display: grid; grid-template-columns: minmax(0,1.42fr) minmax(340px,.88fr); border-block: 1px solid var(--line); background: rgba(251,250,246,.28); }
.operations-classification { min-width: 0; padding: 12px 24px 12px 0; border-right: 1px solid var(--line); }
.operations-monitor { min-width: 0; padding: 12px 0 12px 24px; }
.competitor-list,.dimension-list { list-style: none; margin: 0; padding: 0; }
.competitor-list { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); column-gap: 18px; }
.competitor-list li { min-width: 0; min-height: 38px; display: grid; grid-template-columns: 8px minmax(0,1fr) auto; gap: 9px; align-items: center; padding: 3px 2px; border-bottom: 1px solid color-mix(in srgb,var(--line) 78%,transparent); }
.competitor-list li > i { width: 6px; height: 6px; border-radius: 50%; background: #b6b8bd; }
.competitor-list li > i.enabled { background: #2ab877; box-shadow: 0 0 8px rgba(42,184,119,.4); }
.competitor-list li > div { min-width: 0; }
.competitor-list strong,.competitor-list small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.competitor-list strong { font-size: 14px; font-weight: 500; }
.competitor-list small { margin-top: 2px; color: var(--muted); font-size: 10px; }
.competitor-list li > span { color: var(--muted); font-size: 11px; }
.admin-subheading--dimensions { margin-top: 8px; }
.dimension-list { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 6px; padding-top: 6px; }
.dimension-list li { min-width: 0; min-height: 40px; display: grid; grid-template-columns: auto 1fr; grid-template-rows: auto auto; gap: 1px 7px; align-content: center; padding: 4px 8px; border-left: 2px solid color-mix(in srgb,var(--accent) 52%,transparent); background: rgba(25,31,42,.025); }
.dimension-list li > span { color: var(--accent); font-family: ui-monospace,monospace; font-size: 11px; }
.dimension-list strong { font-size: 13px; font-weight: 500; }
.dimension-list small { grid-column: 1/-1; color: var(--muted); overflow: hidden; font-family: ui-monospace,monospace; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.monitor-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); margin: 8px 0 0; }
.monitor-grid > div { min-width: 0; min-height: 62px; padding: 9px 12px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); }
.monitor-grid > div:nth-child(2n) { border-right: 0; }
.monitor-grid > div:nth-last-child(-n+2) { border-bottom: 0; }
.monitor-grid dt { color: var(--muted); font-size: 11px; }
.monitor-grid dd { margin: 10px 0 0; overflow: hidden; font-family: ui-monospace,monospace; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.admin-import-section { border-bottom: 0; }
.import-layout { position: relative; display: grid; grid-template-columns: .9fr 1.1fr; border: 1px solid rgba(25,31,42,.24); background: rgba(251,250,246,.3); }
.import-layout > div { padding: 28px; }
.import-layout > div:first-child { border-right: 1px solid var(--line); }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.drop-zone { width: 100%; min-height: 258px; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 28px; color: var(--text); border: 1px dashed rgba(25,31,42,.32); background: rgba(244,241,234,.45); cursor: pointer; transition: border-color .2s,background .2s,transform .2s; }
.drop-zone:hover,.drop-zone.dragging { border-color: var(--accent); background: var(--accent-soft); transform: translateY(-2px); }
.drop-zone:disabled { cursor: wait; transform: none; }
.drop-zone .el-icon { margin-bottom: 21px; color: var(--accent); font-size: 33px; }
.drop-zone strong { max-width: 100%; overflow: hidden; font-size: 16px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.drop-zone span { margin-top: 10px; color: var(--muted); font-size: 12px; }
.drop-zone small { margin-top: 7px; color: rgba(21,23,27,.4); font-size: 10px; }
.remove-file { display: block; margin: 10px 0 0 auto; padding: 0; color: var(--muted); border: 0; background: transparent; cursor: pointer; font-size: 10px; text-decoration: underline; }
.import-steps { display: grid; grid-template-columns: repeat(4,1fr); margin-top: 27px; }
.import-steps > div { position: relative; display: flex; flex-direction: column; align-items: center; gap: 7px; color: var(--muted); font-size: 10px; }
.import-steps > div::before { content: ""; position: absolute; top: 10px; right: 50%; left: -50%; height: 1px; background: var(--line); }
.import-steps > div:first-child::before { display: none; }
.import-steps i { position: relative; z-index: 1; width: 21px; height: 21px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 50%; background: var(--paper); font-family: ui-monospace,monospace; font-style: normal; font-size: 10px; }
.import-steps > div.active,.import-steps > div.done { color: var(--accent); }
.import-steps > div.active i,.import-steps > div.done i { color: white; border-color: var(--accent); background: var(--accent); box-shadow: 0 0 0 4px var(--accent-soft); }
.import-progress { margin-top: 20px; }
.import-progress > i { display: block; height: 3px; overflow: hidden; background: rgba(25,31,42,.08); }
.import-progress b { display: block; height: 100%; background: var(--accent); transition: width .25s; }
.import-progress span { display: block; margin-top: 7px; color: var(--muted); font-size: 10px; text-align: right; }
.import-form { display: flex; flex-direction: column; gap: 18px; }
.import-form label { display: grid; gap: 7px; }
.import-form label > span { color: var(--muted); font-size: 11px; }
.import-form .el-select,.import-form .el-date-editor { width: 100%; }
.import-form__row { display: grid; grid-template-columns: .72fr 1.28fr; gap: 14px; }
.form-defaults { margin: -2px 0 4px; color: var(--muted); font-family: ui-monospace,monospace; font-size: 10px; }
.jsonl-notice { min-height: 270px; display: flex; flex-direction: column; justify-content: center; padding: 32px; border-block: 1px solid var(--line); }
.jsonl-notice > span { color: var(--accent); font-family: ui-monospace,monospace; font-size: 10px; letter-spacing: .15em; }
.jsonl-notice h3 { margin: 15px 0 10px; font-size: 24px; font-weight: 500; }
.jsonl-notice p { max-width: 470px; margin: 0; color: var(--muted); font-size: 12px; line-height: 1.8; }
.import-submit { min-height: 52px; display: flex; justify-content: space-between; align-items: center; margin-top: auto; padding: 0 20px; color: white; border: 0; background: #161a21; cursor: pointer; transition: background .2s; }
.import-submit:hover { background: var(--accent); }
.import-submit:disabled { color: rgba(255,255,255,.45); background: rgba(22,26,33,.4); cursor: not-allowed; }
.import-submit i { font-style: normal; font-size: 19px; }
.import-result { display: grid; grid-template-columns: 1fr 1.4fr; gap: 28px; margin-top: 18px; padding: 22px 24px; color: #14563b; border: 1px solid rgba(42,184,119,.28); background: rgba(42,184,119,.07); }
.import-result.partial { color: #8b5b13; border-color: rgba(216,162,56,.3); background: rgba(216,162,56,.08); }
.import-result.failed { display: block; color: #95443d; border-color: rgba(214,107,98,.3); background: rgba(214,107,98,.07); }
.import-result strong { font-size: 14px; font-weight: 500; }
.import-result p { margin: 6px 0 0; font-size: 11px; line-height: 1.6; }
.import-result dl { display: grid; grid-template-columns: repeat(5,1fr); margin: 0; }
.import-result dl > div { padding: 0 12px; border-left: 1px solid currentColor; }
.import-result dt { font-size: 10px; opacity: .7; }
.import-result dd { margin: 7px 0 0; font-size: 22px; }
.import-result__errors { grid-column: 1/-1; }
@media (max-width: 1100px) {
  .admin-service-grid { grid-template-columns: repeat(3,1fr); }
  .admin-service-grid article:nth-child(3) { border-right: 0; }
  .admin-service-grid article:nth-child(-n+3) { border-bottom: 1px solid var(--line); }
  .admin-content-grid,.import-layout { grid-template-columns: 1fr; }
  .admin-metrics,.import-layout > div:first-child { border-right: 0; border-bottom: 1px solid var(--line); }
  .operations-grid { grid-template-columns: minmax(0,1.2fr) minmax(300px,.8fr); }
  .operations-classification { padding-right: 18px; }
  .operations-monitor { padding-left: 18px; }
  .dimension-list { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .import-result { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .admin-page { padding-bottom: 48px; }
  .admin-intro { min-height: 300px; display: block; padding-top: 48px; }
  .admin-intro h1 { font-size: 34px; line-height: 1.12; }
  .admin-refresh { min-width: 0; justify-items: start; margin-top: 28px; text-align: left; }
  .admin-service-grid { grid-template-columns: repeat(2,1fr); }
  .admin-service-grid article { min-height: 130px; padding: 18px; }
  .admin-service-grid article:nth-child(3) { border-right: 1px solid var(--line); }
  .admin-service-grid article:nth-child(2n) { border-right: 0; }
  .admin-service-grid article:nth-child(-n+4) { border-bottom: 1px solid var(--line); }
  .admin-service-grid h2 { margin-top: 30px; }
  .admin-section { padding: 50px 0; }
  .admin-section__heading { display: block; }
  .admin-section__heading > small { display: block; margin: 10px 0 0 29px; }
  .admin-section__actions { justify-items: start; margin: 12px 0 0 34px; }
  .admin-documents-cta { width: 100%; justify-content: space-between; }
  .admin-content-grid { border-bottom: 0; }
  .admin-metrics { grid-template-columns: 1fr 1fr; }
  .admin-metrics a { min-height: 130px; padding: 20px 16px; }
  .admin-metrics a > i { display: none; }
  .admin-metrics strong { font-size: 32px; }
  .admin-operations-section { padding: 28px 0 32px; }
  .operations-grid { grid-template-columns: 1fr; }
  .operations-classification { padding: 15px 0 18px; border-right: 0; border-bottom: 1px solid var(--line); }
  .operations-monitor { padding: 15px 0 10px; }
  .competitor-list { column-gap: 10px; }
  .competitor-list li { grid-template-columns: 7px minmax(0,1fr); }
  .competitor-list li > span { grid-column: 2; }
  .dimension-list { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .import-layout > div { padding: 18px; }
  .drop-zone { min-height: 220px; padding: 18px; }
  .import-form__row { grid-template-columns: 1fr; }
  .import-result dl { grid-template-columns: repeat(2,1fr); gap: 17px 0; }
  .admin-table-wrap { margin-inline: -16px; padding-inline: 16px; }
  table { min-width: 700px; }
}
</style>
