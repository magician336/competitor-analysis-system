<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import PageHeader from "../components/PageHeader.vue";
import LoadState from "../components/LoadState.vue";
import { briefingApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import { renderMarkdown } from "../utils/markdown";
import type { BriefingDetail, BriefingSummary } from "../types/api";

const store = useAppStore();
const loading = ref(false);
const previewLoading = ref(false);
const error = ref("");
const items = ref<BriefingSummary[]>([]);
const selected = ref<BriefingDetail | null>(null);
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const filters = reactive({ competitor: "", workflow_id: "", snapshot_id: "" });
const renderedMarkdown = computed(() => selected.value ? renderMarkdown(selected.value.markdown) : "");

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const response = await briefingApi.list({ page: page.value, page_size: pageSize.value, ...filters });
    items.value = response.items;
    total.value = response.total;
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}
function search() { page.value = 1; void load(); }
async function preview(id: string) {
  previewLoading.value = true;
  try { selected.value = await briefingApi.detail(id); }
  catch (reason) { ElMessage.error(errorMessage(reason)); }
  finally { previewLoading.value = false; }
}
function previewRow(row: BriefingSummary) { void preview(row.briefing_id); }
async function download(id: string) {
  try { await briefingApi.download(id); }
  catch (reason) { ElMessage.error(errorMessage(reason)); }
}
onMounted(async () => {
  if (!store.competitors.length) await store.loadCompetitors();
  await load();
});
</script>

<template>
  <PageHeader title="竞争简报" description="按需加载 Markdown 正文，支持安全预览和原文件下载" />
  <div class="two-column">
    <section class="panel">
      <div class="filter-bar">
        <el-select v-model="filters.competitor" clearable placeholder="竞品" style="width:145px">
          <el-option v-for="item in store.competitors" :key="item.id" :label="item.name" :value="item.name" />
        </el-select>
        <el-input v-model="filters.workflow_id" clearable placeholder="Workflow ID" style="width:190px" />
        <el-input v-model="filters.snapshot_id" clearable placeholder="Snapshot ID" style="width:190px" />
        <el-button type="primary" @click="search">查询</el-button>
      </div>
      <LoadState :loading="loading" :error="error" :empty="!items.length" @retry="load">
        <el-table :data="items" @row-click="previewRow">
          <el-table-column prop="competitor" label="竞品" width="110" />
          <el-table-column prop="briefing_id" label="简报 ID" min-width="190" show-overflow-tooltip />
          <el-table-column prop="created_at" label="生成时间" min-width="170" />
          <el-table-column label="操作" width="90"><template #default="scope"><el-button link type="primary" @click.stop="download(scope.row.briefing_id)">下载</el-button></template></el-table-column>
        </el-table>
        <div class="pagination"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="load" /></div>
      </LoadState>
    </section>
    <section class="panel">
      <div class="filter-bar"><h3 class="panel-title" style="margin:0 auto 0 0">简报预览</h3><el-button v-if="selected" @click="download(selected.briefing_id)">下载 Markdown</el-button></div>
      <div v-if="previewLoading" class="state-panel" v-loading="true">加载正文...</div>
      <el-empty v-else-if="!selected" description="请选择一份简报" />
      <template v-else>
        <el-descriptions :column="1" border size="small" style="margin-bottom:16px">
          <el-descriptions-item label="竞品">{{ selected.competitor }}</el-descriptions-item>
          <el-descriptions-item label="Workflow">{{ selected.workflow_id || '—' }}</el-descriptions-item>
          <el-descriptions-item label="Snapshot">{{ selected.snapshot_id || '—' }}</el-descriptions-item>
        </el-descriptions>
        <article class="markdown-body" v-html="renderedMarkdown" />
      </template>
    </section>
  </div>
</template>
