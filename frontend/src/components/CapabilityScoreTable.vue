<script setup lang="ts">
import { computed } from "vue";
import { capabilityLabel, capabilitySummary, missingCapabilities, scoredCapabilities } from "../utils/capability";
import type { CapabilitySnapshot } from "../types/api";

const props = defineProps<{ snapshot: CapabilitySnapshot }>();
const scored = computed(() => scoredCapabilities(props.snapshot));
const missing = computed(() => missingCapabilities(props.snapshot));
const summary = computed(() => capabilitySummary(props.snapshot));
</script>

<template>
  <div class="capability-score-view">
    <div class="capability-summary-grid">
      <div><span>已评估维度</span><strong>{{ summary.scoredCount }} / {{ summary.totalCount }}</strong></div>
      <div><span>证据总数</span><strong>{{ summary.evidenceTotal }}</strong></div>
      <div><span>平均置信度</span><strong>{{ Math.round(summary.averageConfidence * 100) }}%</strong></div>
      <div><span>数据覆盖率</span><strong>{{ Math.round(summary.coverageRatio * 100) }}%</strong></div>
    </div>

    <div v-if="scored.length" class="capability-table-wrap">
      <table class="capability-table">
        <thead><tr><th>能力维度</th><th>本次分数</th><th>置信度</th><th>证据数</th></tr></thead>
        <tbody>
          <tr v-for="item in scored" :key="item.dimension" data-status="scored">
            <td><b>{{ capabilityLabel(item.dimension).code }}</b> {{ capabilityLabel(item.dimension).name }}</td>
            <td>
              <div class="score-cell"><strong>{{ item.score.toFixed(0) }}</strong><span><i :style="{ width: `${item.score}%` }" /></span></div>
            </td>
            <td><span :class="{ 'low-confidence': item.confidence < 0.5 }">{{ Math.round(item.confidence * 100) }}%</span><small v-if="item.confidence < 0.5">低置信度</small></td>
            <td>{{ item.evidence_count }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="capability-empty">本次没有获得可评分的能力维度</div>

    <div v-if="missing.length" class="capability-missing">
      <strong>本次证据不足</strong>
      <span v-for="item in missing" :key="item.dimension" data-status="insufficient_evidence">
        {{ capabilityLabel(item.dimension).code }} {{ capabilityLabel(item.dimension).name }} · N/A
      </span>
    </div>
  </div>
</template>
