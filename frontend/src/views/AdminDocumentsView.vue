<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ArrowLeft, Refresh, Search } from "@element-plus/icons-vue";
import { useRoute, useRouter } from "vue-router";
import { adminApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import {
  DIMENSIONS,
  type AdminDocumentListItem,
  type AdminDocumentStatus,
  type AdminOverview,
  type Page
} from "../types/api";

const PAGE_SIZE = 20;
const VALID_STATUSES: AdminDocumentStatus[] = ["all", "current", "historical", "review"];

const route = useRoute();
const router = useRouter();
const store = useAppStore();
const result = ref<Page<AdminDocumentListItem> | null>(null);
const overview = ref<AdminOverview | null>(null);
const loading = ref(false);
const loadError = ref("");
const searchText = ref("");
const competitor = ref("");
const sourceType = ref("");
const status = ref<AdminDocumentStatus>("all");
let requestSequence = 0;

const items = computed(() => result.value?.items ?? []);
const currentPage = computed(() => result.value?.page ?? parsePage(route.query.page));
const totalPages = computed(() => result.value?.total_pages ?? 0);
const total = computed(() => result.value?.total ?? 0);
const competitorOptions = computed(() => {
  const names = new Set([
    ...Object.keys(overview.value?.distributions.competitors ?? {}),
    ...store.competitors.map((item) => item.name)
  ]);
  return [...names].sort((a, b) => a.localeCompare(b, "zh-CN"));
});
const sourceOptions = computed(() =>
  Object.keys(overview.value?.distributions.source_types ?? {}).sort((a, b) => a.localeCompare(b))
);
const hasFilters = computed(() =>
  Boolean(searchText.value.trim() || competitor.value || sourceType.value || status.value !== "all")
);

function queryValue(value: unknown) {
  if (Array.isArray(value)) return String(value[0] ?? "");
  return typeof value === "string" ? value : "";
}

function parsePage(value: unknown) {
  const parsed = Number(queryValue(value) || 1);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
}

function parseStatus(value: unknown): AdminDocumentStatus {
  const candidate = queryValue(value) as AdminDocumentStatus;
  return VALID_STATUSES.includes(candidate) ? candidate : "all";
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

function dimensionLabel(value: string) {
  const dimension = DIMENSIONS.find((item) => item.key === value);
  return dimension ? `${dimension.code} · ${dimension.name}` : value;
}

function formatDate(value?: string | null) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).format(parsed);
}

function syncFiltersFromRoute() {
  searchText.value = queryValue(route.query.q);
  competitor.value = queryValue(route.query.competitor);
  sourceType.value = queryValue(route.query.source_type);
  status.value = parseStatus(route.query.status);
}

async function loadDocuments() {
  const sequence = ++requestSequence;
  loading.value = true;
  loadError.value = "";
  try {
    const data = await adminApi.documents({
      page: parsePage(route.query.page),
      page_size: PAGE_SIZE,
      q: queryValue(route.query.q),
      status: parseStatus(route.query.status),
      competitor: queryValue(route.query.competitor),
      source_type: queryValue(route.query.source_type)
    });
    if (sequence === requestSequence) result.value = data;
  } catch (reason) {
    if (sequence === requestSequence) {
      result.value = null;
      loadError.value = errorMessage(reason);
    }
  } finally {
    if (sequence === requestSequence) loading.value = false;
  }
}

function applyFilters() {
  void router.push({
    path: "/admin/documents",
    query: {
      q: searchText.value.trim() || undefined,
      competitor: competitor.value || undefined,
      source_type: sourceType.value || undefined,
      status: status.value === "all" ? undefined : status.value
    }
  });
}

function clearFilters() {
  searchText.value = "";
  competitor.value = "";
  sourceType.value = "";
  status.value = "all";
  void router.push("/admin/documents");
}

function changePage(page: number) {
  void router.push({
    path: "/admin/documents",
    query: {
      ...route.query,
      page: page > 1 ? String(page) : undefined
    }
  });
}

watch(
  () => route.fullPath,
  () => {
    syncFiltersFromRoute();
    void loadDocuments();
  },
  { immediate: true }
);

onMounted(() => {
  if (!store.competitors.length) void store.loadCompetitors().catch(() => undefined);
  void adminApi.overview().then((data) => {
    overview.value = data;
  }).catch(() => undefined);
});
</script>

<template>
  <div class="documents-page route-grid">
    <div class="documents-page__inner">
      <header class="documents-intro">
        <router-link to="/admin" class="documents-back">
          <el-icon><ArrowLeft /></el-icon>
          返回系统管理
        </router-link>
        <p class="eyebrow">CONTENT LIBRARY / READ ONLY</p>
        <div class="documents-intro__title">
          <div>
            <h1>全部文档</h1>
            <p>浏览证据库中的全部文档与版本，支持搜索、筛选和内容预览。</p>
          </div>
          <div class="documents-total">
            <span>匹配文档</span>
            <strong>{{ total.toLocaleString("zh-CN") }}</strong>
            <small>每页 {{ PAGE_SIZE }} 条</small>
          </div>
        </div>
      </header>

      <section class="document-controls" aria-label="文档筛选">
        <form class="document-search" @submit.prevent="applyFilters">
          <el-input v-model="searchText" clearable placeholder="按文档标题搜索" aria-label="按文档标题搜索" @clear="applyFilters">
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <button type="submit">搜索</button>
        </form>
        <div class="document-filters">
          <label>
            <span>版本状态</span>
            <el-select v-model="status" aria-label="版本状态" @change="applyFilters">
              <el-option label="全部状态" value="all" />
              <el-option label="当前版本" value="current" />
              <el-option label="历史版本" value="historical" />
              <el-option label="待审核" value="review" />
            </el-select>
          </label>
          <label>
            <span>所属竞品</span>
            <el-select v-model="competitor" clearable filterable placeholder="全部竞品" aria-label="所属竞品" @change="applyFilters">
              <el-option v-for="item in competitorOptions" :key="item" :label="item" :value="item" />
            </el-select>
          </label>
          <label>
            <span>来源类型</span>
            <el-select v-model="sourceType" clearable placeholder="全部来源" aria-label="来源类型" @change="applyFilters">
              <el-option v-for="item in sourceOptions" :key="item" :label="sourceLabel(item)" :value="item" />
            </el-select>
          </label>
          <button v-if="hasFilters" type="button" class="clear-filters" @click="clearFilters">清除筛选</button>
        </div>
      </section>

      <section class="document-results" aria-live="polite">
        <div class="document-results__heading">
          <div>
            <span>{{ status === "current" ? "当前版本" : status === "historical" ? "历史版本" : status === "review" ? "待审核" : "全部文档" }}</span>
            <small v-if="result">第 {{ currentPage }} / {{ Math.max(totalPages, 1) }} 页</small>
          </div>
          <button type="button" :disabled="loading" aria-label="刷新文档" @click="loadDocuments">
            <el-icon><Refresh /></el-icon>
            刷新
          </button>
        </div>

        <div v-if="loading" class="document-state">
          <i class="document-loader" />
          <strong>正在读取文档</strong>
          <span>请稍候</span>
        </div>
        <div v-else-if="loadError" class="document-state document-state--error" role="alert">
          <strong>文档暂时无法加载</strong>
          <span>{{ loadError }}</span>
          <button type="button" @click="loadDocuments">重新加载</button>
        </div>
        <div v-else-if="!items.length" class="document-state">
          <strong>没有找到符合条件的文档</strong>
          <span>可以调整搜索词或筛选条件后再试。</span>
          <button v-if="hasFilters" type="button" @click="clearFilters">查看全部文档</button>
        </div>
        <div v-else class="document-table-wrap">
          <table class="document-table">
            <thead>
              <tr>
                <th>文档</th>
                <th>竞品</th>
                <th>来源</th>
                <th>发布时间</th>
                <th>能力标签</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="`${item.document_id}-${item.version_id}`">
                <td>
                  <strong :title="item.title">{{ item.title }}</strong>
                  <small>{{ item.document_id }} · {{ item.version_id }}</small>
                </td>
                <td>{{ item.competitor }}</td>
                <td>{{ sourceLabel(item.source_type) }}</td>
                <td>{{ formatDate(item.publish_time) }}</td>
                <td>
                  <div v-if="item.dimension_tags.length" class="document-dimensions">
                    <span v-for="tag in item.dimension_tags" :key="tag">{{ dimensionLabel(tag) }}</span>
                  </div>
                  <span v-else class="document-muted">—</span>
                </td>
                <td>
                  <div class="document-statuses">
                    <span class="version-state" :class="{ historical: !item.is_current }">{{ item.is_current ? "当前" : "历史" }}</span>
                    <span v-if="item.needs_review" class="review-state">待审核</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="result && totalPages > 1 && !loading" class="document-pagination">
          <span>共 {{ total.toLocaleString("zh-CN") }} 条</span>
          <el-pagination
            background
            layout="prev, pager, next"
            :current-page="currentPage"
            :page-size="PAGE_SIZE"
            :total="total"
            @current-change="changePage"
          />
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.documents-page { padding-bottom: 96px; }
.documents-page__inner { width: min(100%, 1280px); margin: 0 auto; }
.documents-intro { min-height: 300px; padding: 44px 0 42px; border-bottom: 1px solid rgba(25,31,42,.28); }
.documents-back { display: inline-flex; align-items: center; gap: 7px; margin-bottom: 42px; color: var(--muted); font-size: 11px; text-decoration: none; }
.documents-back:hover { color: var(--accent); }
.documents-intro__title { display: flex; justify-content: space-between; align-items: flex-end; gap: 42px; }
.documents-intro h1 { margin: 12px 0 10px; font-size: clamp(42px,5vw,68px); font-weight: 400; letter-spacing: -.04em; }
.documents-intro__title p { max-width: 680px; margin: 0; color: var(--muted); font-size: 14px; line-height: 1.75; }
.documents-total { min-width: 150px; text-align: right; }
.documents-total span,.documents-total small { display: block; color: var(--muted); font-size: 10px; }
.documents-total strong { display: block; margin: 7px 0; font-size: 40px; font-weight: 400; line-height: 1; }
.document-controls { display: grid; grid-template-columns: minmax(280px,.7fr) 1.3fr; gap: 42px; padding: 28px 0; border-bottom: 1px solid rgba(25,31,42,.28); }
.document-search { display: grid; grid-template-columns: 1fr auto; align-self: end; }
.document-search :deep(.el-input__wrapper) { border-radius: 0; box-shadow: 0 0 0 1px var(--line) inset; }
.document-search button { min-width: 76px; color: white; border: 0; background: #161a21; cursor: pointer; }
.document-search button:hover { background: var(--accent); }
.document-filters { display: grid; grid-template-columns: repeat(3,minmax(130px,1fr)) auto; gap: 12px; align-items: end; }
.document-filters label { min-width: 0; display: grid; gap: 7px; }
.document-filters label > span { color: var(--muted); font-size: 10px; }
.document-filters :deep(.el-select) { width: 100%; }
.clear-filters { height: 32px; padding: 0 4px; color: var(--muted); border: 0; background: transparent; cursor: pointer; font-size: 10px; text-decoration: underline; white-space: nowrap; }
.document-results { padding-top: 40px; }
.document-results__heading { display: flex; justify-content: space-between; align-items: center; padding-bottom: 13px; border-bottom: 1px solid var(--line); }
.document-results__heading > div { display: flex; align-items: baseline; gap: 12px; }
.document-results__heading span { font-size: 15px; font-weight: 500; }
.document-results__heading small { color: var(--muted); font-size: 10px; }
.document-results__heading button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 0; color: var(--muted); border: 0; background: transparent; cursor: pointer; font-size: 10px; }
.document-results__heading button:disabled { cursor: wait; opacity: .45; }
.document-table-wrap { overflow-x: auto; }
.document-table { width: 100%; min-width: 980px; border-collapse: collapse; }
.document-table th { padding: 12px 10px; color: var(--muted); font-size: 10px; font-weight: 400; text-align: left; }
.document-table td { padding: 17px 10px; border-top: 1px solid var(--line); font-size: 12px; vertical-align: middle; }
.document-table td:first-child { width: 35%; }
.document-table td:nth-child(5) { width: 18%; }
.document-table td > strong,.document-table td > small { display: block; }
.document-table td > strong { max-width: 430px; overflow: hidden; font-size: 14px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.document-table td > small { max-width: 390px; margin-top: 5px; overflow: hidden; color: var(--muted); font-family: ui-monospace,monospace; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }
.document-dimensions,.document-statuses { display: flex; flex-wrap: wrap; gap: 5px; }
.document-dimensions span { padding: 3px 6px; color: #315f91; border: 1px solid rgba(47,140,255,.2); background: rgba(47,140,255,.05); font-size: 9px; white-space: nowrap; }
.version-state,.review-state { padding: 4px 8px; border-radius: 999px; font-size: 10px; white-space: nowrap; }
.version-state { color: #127149; border: 1px solid rgba(18,113,73,.25); }
.version-state.historical { color: var(--muted); border-color: var(--line); }
.review-state { color: #9a6515; border: 1px solid rgba(216,162,56,.35); background: rgba(216,162,56,.06); }
.document-muted { color: var(--muted); }
.document-state { min-height: 320px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--muted); text-align: center; }
.document-state strong { color: var(--text); font-size: 15px; font-weight: 500; }
.document-state span { font-size: 11px; }
.document-state button { margin-top: 8px; padding: 8px 15px; color: var(--text); border: 1px solid var(--line); background: transparent; cursor: pointer; }
.document-state--error strong { color: #95443d; }
.document-loader { width: 22px; height: 22px; margin-bottom: 8px; border: 2px solid rgba(47,140,255,.16); border-top-color: var(--accent); border-radius: 50%; animation: document-spin .8s linear infinite; }
.document-pagination { display: flex; justify-content: space-between; align-items: center; padding-top: 24px; border-top: 1px solid var(--line); }
.document-pagination > span { color: var(--muted); font-size: 10px; }
.document-pagination :deep(.el-pager li.is-active) { background: var(--accent); }
@keyframes document-spin { to { transform: rotate(360deg); } }
@media (max-width: 920px) {
  .document-controls { grid-template-columns: 1fr; gap: 20px; }
}
@media (max-width: 680px) {
  .documents-page { padding-bottom: 48px; }
  .documents-intro { min-height: 330px; }
  .documents-intro__title { display: block; }
  .documents-intro h1 { font-size: 48px; }
  .documents-total { margin-top: 26px; text-align: left; }
  .document-filters { grid-template-columns: 1fr 1fr; }
  .document-filters label:first-child { grid-column: 1 / -1; }
  .document-table-wrap { margin-inline: -16px; padding-inline: 16px; }
  .document-pagination { align-items: flex-start; gap: 16px; }
  .document-pagination > span { display: none; }
}
</style>
