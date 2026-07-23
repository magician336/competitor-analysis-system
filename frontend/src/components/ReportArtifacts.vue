<script setup lang="ts">
import { computed, ref } from "vue";
import StatusTag from "./StatusTag.vue";
import { capabilitySummary } from "../utils/capability";
import { DIMENSIONS } from "../types/api";
import type { BriefingDetail, CardDetail, SnapshotDetail, WorkflowDetail } from "../types/api";

const props = defineProps<{
  detail: WorkflowDetail;
  briefing: BriefingDetail;
  snapshot: SnapshotDetail | null;
  cards: CardDetail[];
  timeRangeLabel: string;
}>();
const emit = defineEmits<{ back: []; download: [] }>();
const opened = ref<string[]>(["snapshot"]);
const summary = computed(() => props.snapshot ? capabilitySummary(props.snapshot.snapshot) : null);
const modeLabel = computed(() => ({ rules: "快速分析", hybrid: "AI 辅助分析", llm: "深度 AI 分析" })[props.detail.analysis_mode]);
const dimensionLabel = (key: string) => {
  const item = DIMENSIONS.find((dimension) => dimension.key === key);
  return item ? `${item.code} ${item.name}` : key;
};
</script>

<template>
  <div class="report-artifacts">
    <div class="report-sidebar__top">
      <button type="button" class="artifact-back" @click="emit('back')">← 返回报告记录</button>
      <span class="module-kicker">MARKDOWN REPORT</span>
      <h1>{{ detail.competitor }}<br>模型趋势分析简报</h1>
      <div class="report-sidebar__meta"><StatusTag :status="detail.status" /><span>{{ modeLabel }}</span><span>{{ timeRangeLabel }}</span><span>{{ briefing.created_at }}</span></div>
      <button type="button" class="artifact-download" @click="emit('download')">下载 Markdown <span>↓</span></button>
    </div>

    <div class="artifact-overview">
      <div><small>情报卡片</small><strong>{{ cards.length }}</strong></div>
      <div><small>覆盖率</small><strong>{{ summary ? `${Math.round(summary.coverageRatio * 100)}%` : "N/A" }}</strong></div>
      <div><small>执行模式</small><strong>{{ detail.analysis_mode.toUpperCase() }}</strong></div>
    </div>

    <el-collapse v-model="opened" class="artifact-collapse">
      <el-collapse-item name="snapshot">
        <template #title><span class="artifact-title"><b>01</b><span>能力快照<small>{{ summary ? `已评估 ${summary.scoredCount}/${summary.totalCount}` : "未生成" }}</small></span></span></template>
        <div v-if="snapshot && summary" class="artifact-metrics">
          <div><span>总分</span><b>{{ snapshot.snapshot.total_score.toFixed(1) }}</b></div>
          <div><span>平均置信度</span><b>{{ Math.round(summary.averageConfidence * 100) }}%</b></div>
          <div><span>证据数</span><b>{{ summary.evidenceTotal }}</b></div>
          <p v-if="snapshot.source_kind === 'product_definition'">产品设计基线 · 非实测结果</p>
        </div>
        <p v-else class="muted">本次分析未生成能力快照。</p>
      </el-collapse-item>

      <el-collapse-item name="cards">
        <template #title><span class="artifact-title"><b>02</b><span>情报卡片<small>{{ cards.length }} 项结构化发现</small></span></span></template>
        <div v-if="cards.length" class="artifact-card-list">
          <details v-for="card in cards" :key="card.card.card_id">
            <summary><span>{{ card.card.agent_kind }}</span><strong>{{ card.card.event_title }}</strong><small>{{ Math.round(card.card.confidence_score * 100) }}% · {{ card.evidence_links.length }} 条来源</small></summary>
            <p>{{ card.card.summary }}</p>
            <a v-for="item in card.evidence_links" :key="item.chunk_id" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }} ↗</a>
          </details>
        </div>
        <p v-else class="muted">本次分析没有生成情报卡片。</p>
      </el-collapse-item>

      <el-collapse-item name="scores">
        <template #title><span class="artifact-title"><b>03</b><span>D1–D7 评分<small>保留 N/A 与低置信度</small></span></span></template>
        <div v-if="snapshot" class="artifact-score-list">
          <div v-for="score in snapshot.snapshot.details" :key="score.dimension" :data-status="score.status">
            <span>{{ dimensionLabel(score.dimension) }}</span>
            <strong>{{ score.status === 'scored' ? score.score : 'N/A' }}</strong>
            <small>{{ score.status === 'scored' ? `${Math.round(score.confidence * 100)}% 置信度 · ${score.evidence_count} 条证据` : '证据不足' }}</small>
          </div>
        </div>
        <p v-else class="muted">没有可展示的评分数据。</p>
      </el-collapse-item>

      <el-collapse-item name="runtime">
        <template #title><span class="artifact-title"><b>04</b><span>高级运行详情<small>Workflow、分支和 Trace</small></span></span></template>
        <dl class="runtime-list">
          <div><dt>Workflow</dt><dd class="mono">{{ detail.workflow_id }}</dd></div>
          <div><dt>尝试次数</dt><dd>{{ detail.attempt_count }}/{{ detail.max_attempts }}</dd></div>
          <div v-for="branch in detail.branches" :key="branch.branch"><dt>{{ branch.branch }}</dt><dd><StatusTag :status="branch.status" /><small v-if="branch.duration_ms">{{ branch.duration_ms }} ms</small><span v-if="branch.trace_id" class="mono">{{ branch.trace_id }}</span></dd></div>
        </dl>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>
