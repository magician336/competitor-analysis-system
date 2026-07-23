<script setup lang="ts">
import { onMounted, ref } from "vue";
import PageHeader from "../components/PageHeader.vue";
import LoadState from "../components/LoadState.vue";
import StatusTag from "../components/StatusTag.vue";
import { briefingApi, cardApi, snapshotApi, workflowApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import type { BriefingSummary, CardSummary, SnapshotSummary, WorkflowSummary } from "../types/api";

const store = useAppStore();
const loading = ref(false);
const error = ref("");
const totals = ref({ competitors: 0, cards: 0, snapshots: 0, briefings: 0 });
const latestWorkflow = ref<WorkflowSummary | null>(null);
const latestCard = ref<CardSummary | null>(null);
const latestSnapshot = ref<SnapshotSummary | null>(null);
const latestBriefing = ref<BriefingSummary | null>(null);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await Promise.all([store.refreshStatus(), store.loadCompetitors()]);
    const [cards, snapshots, workflows, briefings] = await Promise.all([
      cardApi.list({ page: 1, page_size: 1 }),
      snapshotApi.list({ page: 1, page_size: 1 }),
      workflowApi.list({ page: 1, page_size: 1 }),
      briefingApi.list({ page: 1, page_size: 1 })
    ]);
    totals.value = {
      competitors: store.competitors.length,
      cards: cards.total,
      snapshots: snapshots.total,
      briefings: briefings.total
    };
    latestCard.value = cards.items[0] || null;
    latestSnapshot.value = snapshots.items[0] || null;
    latestWorkflow.value = workflows.items[0] || null;
    latestBriefing.value = briefings.items[0] || null;
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <PageHeader title="系统概览" description="汇总当前情报、能力快照和异步任务运行状态">
    <el-button @click="load">刷新</el-button>
  </PageHeader>
  <LoadState :loading="loading" :error="error" @retry="load">
    <div class="summary-grid">
      <div class="summary-card"><div class="summary-card__label">竞品</div><div class="summary-card__value">{{ totals.competitors }}</div></div>
      <div class="summary-card"><div class="summary-card__label">情报卡片</div><div class="summary-card__value">{{ totals.cards }}</div></div>
      <div class="summary-card"><div class="summary-card__label">能力快照</div><div class="summary-card__value">{{ totals.snapshots }}</div></div>
      <div class="summary-card"><div class="summary-card__label">竞争简报</div><div class="summary-card__value">{{ totals.briefings }}</div></div>
    </div>
    <div class="two-column">
      <section class="panel">
        <h3 class="panel-title">系统状态</h3>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="API"><StatusTag :status="store.apiOnline ? 'success' : 'failed'" /></el-descriptions-item>
          <el-descriptions-item label="Elasticsearch"><StatusTag :status="store.ready?.backend.status || 'unknown'" /></el-descriptions-item>
          <el-descriptions-item label="索引">{{ store.ready?.index || "—" }}</el-descriptions-item>
          <el-descriptions-item label="检索切片">{{ store.ready?.indexed_chunks ?? "—" }}</el-descriptions-item>
        </el-descriptions>
      </section>
      <section class="panel">
        <h3 class="panel-title">最新 Workflow</h3>
        <el-empty v-if="!latestWorkflow" description="暂无 Workflow" />
        <el-descriptions v-else :column="1" border>
          <el-descriptions-item label="竞品">{{ latestWorkflow.competitor }}</el-descriptions-item>
          <el-descriptions-item label="状态"><StatusTag :status="latestWorkflow.status" /></el-descriptions-item>
          <el-descriptions-item label="进度">{{ latestWorkflow.progress }}%</el-descriptions-item>
          <el-descriptions-item label="提交时间">{{ latestWorkflow.submitted_at || "—" }}</el-descriptions-item>
        </el-descriptions>
      </section>
    </div>
    <section class="panel">
      <h3 class="panel-title">最近产物</h3>
      <el-table :data="[
        { type: '情报卡片', name: latestCard?.event_title, owner: latestCard?.competitor, time: latestCard?.created_at },
        { type: '能力快照', name: latestSnapshot?.snapshot_id, owner: latestSnapshot?.competitor, time: latestSnapshot?.created_at },
        { type: '竞争简报', name: latestBriefing?.briefing_id, owner: latestBriefing?.competitor, time: latestBriefing?.created_at }
      ].filter(item => item.name)">
        <el-table-column prop="type" label="类型" width="110" />
        <el-table-column prop="name" label="名称" min-width="260" />
        <el-table-column prop="owner" label="竞品" width="140" />
        <el-table-column prop="time" label="时间" min-width="180" />
      </el-table>
    </section>
  </LoadState>
</template>
