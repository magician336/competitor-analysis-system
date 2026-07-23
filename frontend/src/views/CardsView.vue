<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import PageHeader from "../components/PageHeader.vue";
import LoadState from "../components/LoadState.vue";
import StatusTag from "../components/StatusTag.vue";
import { cardApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import type { CardDetail, CardSummary, EvidenceDetail, EvidenceReference } from "../types/api";

const store = useAppStore();
const loading = ref(false);
const error = ref("");
const items = ref<CardSummary[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const detailVisible = ref(false);
const detail = ref<CardDetail | null>(null);
const selectedEvidence = ref<EvidenceDetail | null>(null);
const filters = reactive({ competitor: "", agent_kind: "", event_type: "", alert_level: "", review_required: "" as boolean | "", min_confidence: undefined as number | undefined, min_priority: undefined as number | undefined, sort_by: "created_at", order: "desc" });

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const response = await cardApi.list({ page: page.value, page_size: pageSize.value, ...filters });
    items.value = response.items;
    total.value = response.total;
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}

function search() { page.value = 1; void load(); }
function reset() {
  Object.assign(filters, { competitor: "", agent_kind: "", event_type: "", alert_level: "", review_required: "", min_confidence: undefined, min_priority: undefined, sort_by: "created_at", order: "desc" });
  search();
}
async function openCard(id: string) {
  detailVisible.value = true;
  detail.value = null;
  selectedEvidence.value = null;
  try { detail.value = await cardApi.detail(id); }
  catch (reason) { ElMessage.error(errorMessage(reason)); }
}
function openCardRow(row: CardSummary) { void openCard(row.card_id); }
async function openEvidence(reference: EvidenceReference) {
  try { selectedEvidence.value = await cardApi.evidence(reference.chunk_id); }
  catch (reason) { ElMessage.error(errorMessage(reason)); }
}

onMounted(async () => {
  if (!store.competitors.length) await store.loadCompetitors();
  await load();
});
</script>

<template>
  <PageHeader title="情报卡片" description="筛选竞品事件，并追溯每项结论的 Evidence 来源">
    <el-button @click="reset">重置筛选</el-button>
  </PageHeader>
  <section class="panel">
    <div class="filter-bar">
      <el-select v-model="filters.competitor" clearable filterable placeholder="竞品" style="width:150px">
        <el-option v-for="item in store.competitors" :key="item.id" :label="item.name" :value="item.name" />
      </el-select>
      <el-select v-model="filters.agent_kind" clearable placeholder="Agent" style="width:135px">
        <el-option v-for="item in ['price','product','risk']" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="filters.event_type" clearable placeholder="事件类型" style="width:165px">
        <el-option v-for="item in ['pricing_change','product_release','risk_experience']" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="filters.alert_level" clearable placeholder="告警" style="width:120px">
        <el-option v-for="item in ['red','orange','yellow','blue']" :key="item" :label="item" :value="item" />
      </el-select>
      <el-select v-model="filters.review_required" clearable placeholder="复核状态" style="width:130px">
        <el-option label="需要复核" :value="true" /><el-option label="无需复核" :value="false" />
      </el-select>
      <el-input-number v-model="filters.min_confidence" :min="0" :max="1" :step="0.1" placeholder="最低置信度" />
      <el-input-number v-model="filters.min_priority" :min="0" :max="100" placeholder="最低优先级" />
      <el-select v-model="filters.sort_by" style="width:145px"><el-option label="按时间" value="created_at" /><el-option label="按优先级" value="priority_score" /><el-option label="按置信度" value="confidence_score" /></el-select>
      <el-button type="primary" @click="search">查询</el-button>
    </div>
    <LoadState :loading="loading" :error="error" :empty="!items.length" @retry="load">
      <el-table :data="items" @row-click="openCardRow">
        <el-table-column prop="competitor" label="竞品" width="120" />
        <el-table-column prop="event_title" label="事件" min-width="280" show-overflow-tooltip />
        <el-table-column prop="agent_kind" label="Agent" width="90" />
        <el-table-column label="告警" width="90"><template #default="scope"><StatusTag :status="scope.row.alert_level" /></template></el-table-column>
        <el-table-column label="置信度" width="100"><template #default="scope">{{ Math.round(scope.row.confidence_score * 100) }}%</template></el-table-column>
        <el-table-column prop="priority_score" label="优先级" width="90" />
        <el-table-column label="复核" width="80"><template #default="scope"><el-tag v-if="scope.row.review_required" type="warning">需复核</el-tag><span v-else>—</span></template></el-table-column>
        <el-table-column prop="evidence_count" label="证据" width="70" />
        <el-table-column prop="created_at" label="创建时间" min-width="180" />
      </el-table>
      <div class="pagination"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[10,20,50]" layout="total, sizes, prev, pager, next" :total="total" @change="load" /></div>
    </LoadState>
  </section>

  <el-drawer v-model="detailVisible" title="情报卡片详情" size="720px">
    <LoadState :loading="!detail">
      <template v-if="detail">
        <h3>{{ detail.card.event_title }}</h3>
        <p>{{ detail.card.summary }}</p>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="竞品">{{ detail.card.competitor }}</el-descriptions-item>
          <el-descriptions-item label="Agent">{{ detail.card.agent_kind }}</el-descriptions-item>
          <el-descriptions-item label="置信度">{{ Math.round(detail.card.confidence_score * 100) }}%</el-descriptions-item>
          <el-descriptions-item label="优先级">{{ detail.card.priority_score }}</el-descriptions-item>
        </el-descriptions>
        <h3>Evidence（{{ detail.evidence_links.length }}）</h3>
        <el-empty v-if="!detail.evidence_links.length" description="该卡片暂无可追溯证据" />
        <el-card v-for="evidence in detail.evidence_links" :key="evidence.chunk_id" shadow="never" style="margin-bottom:10px; cursor:pointer" @click="openEvidence(evidence)">
          <strong>{{ evidence.title }}</strong>
          <p class="muted">{{ evidence.quote || "暂无引用摘要" }}</p>
          <el-space><el-tag>{{ evidence.evidence_level }}</el-tag><el-tag type="info">{{ evidence.source_type }}</el-tag></el-space>
        </el-card>
        <template v-if="selectedEvidence">
          <el-divider>Evidence 详情</el-divider>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="Chunk"><span class="mono">{{ selectedEvidence.evidence.chunk_id }}</span></el-descriptions-item>
            <el-descriptions-item label="Document"><span class="mono">{{ selectedEvidence.evidence.document_id }}</span></el-descriptions-item>
            <el-descriptions-item label="Version"><span class="mono">{{ selectedEvidence.evidence.version_id }}</span></el-descriptions-item>
            <el-descriptions-item label="关联卡片">{{ selectedEvidence.card_ids.join(', ') }}</el-descriptions-item>
            <el-descriptions-item label="来源"><el-link :href="selectedEvidence.evidence.url" target="_blank" type="primary">打开原始页面</el-link></el-descriptions-item>
          </el-descriptions>
        </template>
      </template>
    </LoadState>
  </el-drawer>
</template>
