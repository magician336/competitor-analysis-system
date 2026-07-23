<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import PageHeader from "../components/PageHeader.vue";
import LoadState from "../components/LoadState.vue";
import StatusTag from "../components/StatusTag.vue";
import TrendChart from "../components/charts/TrendChart.vue";
import GapChart from "../components/charts/GapChart.vue";
import { comparisonApi, snapshotApi } from "../api/services";
import { errorMessage } from "../api/client";
import { DIMENSIONS, type ComparisonResponse, type SnapshotSummary } from "../types/api";

const loading = ref(false);
const error = ref("");
const snapshots = ref<SnapshotSummary[]>([]);
const comparison = ref<ComparisonResponse | null>(null);
const selectedCompetitors = ref<string[]>([]);
const competitors = computed(() => Array.from(new Set(snapshots.value.map((item) => item.competitor))));
const visibleSnapshots = computed(() => snapshots.value.filter((item) => !selectedCompetitors.value.length || selectedCompetitors.value.includes(item.competitor)));
const historyEnough = computed(() => {
  const counts = new Map<string, number>();
  visibleSnapshots.value.forEach((item) => counts.set(item.competitor, (counts.get(item.competitor) || 0) + 1));
  return Array.from(counts.values()).some((count) => count >= 2);
});
const dimensionLabel = (key: string) => {
  const item = DIMENSIONS.find((entry) => entry.key === key);
  return item ? `${item.code} ${item.name}` : key;
};

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [snapshotPage, latest] = await Promise.all([
      snapshotApi.list({ page: 1, page_size: 100 }),
      comparisonApi.latest()
    ]);
    snapshots.value = snapshotPage.items;
    comparison.value = latest;
    selectedCompetitors.value = competitors.value;
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}
onMounted(load);
</script>

<template>
  <PageHeader title="趋势与差距" description="观察能力总分变化、相对基线差距和行业标配推断">
    <el-button @click="load">刷新</el-button>
  </PageHeader>
  <LoadState :loading="loading" :error="error" :empty="!snapshots.length" @retry="load">
    <section class="panel">
      <div class="filter-bar">
        <h3 class="panel-title" style="margin:0 auto 0 0">竞品范围</h3>
        <el-checkbox-group v-model="selectedCompetitors">
          <el-checkbox v-for="item in competitors" :key="item" :value="item">{{ item }}</el-checkbox>
        </el-checkbox-group>
      </div>
    </section>
    <section class="panel">
      <h3 class="panel-title">能力总分时间趋势</h3>
      <TrendChart v-if="historyEnough" :snapshots="visibleSnapshots" />
      <el-empty v-else description="当前每个竞品只有一个快照，历史数据不足，暂不推算趋势" />
    </section>
    <section class="panel">
      <h3 class="panel-title">相对基线差距变化</h3>
      <GapChart v-if="comparison?.matrix.gap_trends.length" :trends="comparison.matrix.gap_trends" />
      <el-empty v-else description="当前比较矩阵没有兼容的前序快照，暂无差距变化数据" />
    </section>
    <section class="panel">
      <h3 class="panel-title">行业标配判断</h3>
      <el-table :data="comparison?.matrix.table_stakes || []">
        <el-table-column label="维度" width="180"><template #default="scope">{{ dimensionLabel(scope.row.dimension) }}</template></el-table-column>
        <el-table-column label="状态" width="130"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
        <el-table-column prop="score_threshold" label="阈值" width="80" />
        <el-table-column prop="valid_product_count" label="有效产品" width="100" />
        <el-table-column label="达标产品" min-width="180"><template #default="scope">{{ scope.row.qualifying_products.join(', ') || '—' }}</template></el-table-column>
        <el-table-column prop="basis" label="依据" min-width="280" show-overflow-tooltip />
      </el-table>
    </section>
  </LoadState>
</template>
