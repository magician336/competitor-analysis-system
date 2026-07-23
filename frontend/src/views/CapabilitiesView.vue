<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import PageHeader from "../components/PageHeader.vue";
import LoadState from "../components/LoadState.vue";
import RadarChart from "../components/charts/RadarChart.vue";
import CapabilityMatrix from "../components/charts/CapabilityMatrix.vue";
import { comparisonApi } from "../api/services";
import { errorMessage } from "../api/client";
import type { ComparisonResponse } from "../types/api";

const loading = ref(false);
const error = ref("");
const comparison = ref<ComparisonResponse | null>(null);
const selectedProducts = ref<string[]>([]);
const products = computed(() => comparison.value?.matrix.products.map((item) => item.product) || []);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    comparison.value = await comparisonApi.latest();
    selectedProducts.value = products.value.slice(0, 6);
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}
onMounted(load);
</script>

<template>
  <PageHeader title="能力分析" description="查看 D1—D7 能力雷达图、竞品矩阵、覆盖率与置信度">
    <el-button @click="load">刷新</el-button>
  </PageHeader>
  <LoadState :loading="loading" :error="error" :empty="!comparison" empty-text="暂无比较矩阵" @retry="load">
    <template v-if="comparison">
      <el-alert v-if="!comparison.official_ranking_ready" class="warning-note" type="warning" :closable="false" title="当前数据不足以形成正式排名；下方矩阵仅用于能力观察。" />
      <el-alert v-if="comparison.matrix.products.some(item => item.product === 'CodeMate Campus')" class="warning-note" type="info" :closable="false" title="CodeMate Campus 为 product_definition 产品设计基线，覆盖率 56%，不代表正式实测结果。" />
      <section class="panel">
        <div class="filter-bar">
          <h3 class="panel-title" style="margin:0 auto 0 0">产品选择</h3>
          <el-checkbox-group v-model="selectedProducts">
            <el-checkbox v-for="product in products" :key="product" :value="product">{{ product }}</el-checkbox>
          </el-checkbox-group>
        </div>
      </section>
      <div class="two-column">
        <section class="panel"><h3 class="panel-title">D1—D7 雷达图</h3><RadarChart :rows="comparison.matrix.rows" :products="selectedProducts" /></section>
        <section class="panel">
          <h3 class="panel-title">产品摘要</h3>
          <el-table :data="comparison.matrix.products.filter(item => selectedProducts.includes(item.product))">
            <el-table-column prop="product" label="产品" min-width="130" />
            <el-table-column label="总分" width="80"><template #default="scope">{{ scope.row.weighted_total_score ?? 'N/A' }}</template></el-table-column>
            <el-table-column label="覆盖率" width="90"><template #default="scope">{{ Math.round(scope.row.coverage_ratio * 100) }}%</template></el-table-column>
            <el-table-column label="置信度" width="90"><template #default="scope">{{ Math.round(scope.row.weighted_confidence * 100) }}%</template></el-table-column>
            <el-table-column label="排名" width="80"><template #default="scope">{{ comparison.official_ranking_ready && scope.row.rank ? scope.row.rank : '—' }}</template></el-table-column>
          </el-table>
        </section>
      </div>
      <section class="panel"><h3 class="panel-title">能力矩阵</h3><CapabilityMatrix :rows="comparison.matrix.rows" :products="selectedProducts" /></section>
    </template>
  </LoadState>
</template>
