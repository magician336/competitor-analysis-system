<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import PageHeader from "../components/PageHeader.vue";
import LoadState from "../components/LoadState.vue";
import StatusTag from "../components/StatusTag.vue";
import { errorMessage } from "../api/client";
import { workflowApi } from "../api/services";
import { createWorkflowPoller, TERMINAL_WORKFLOW_STATUSES } from "../composables/workflowPolling";
import { useAppStore } from "../stores/app";
import type { SpecialistBranch, WorkflowDetail, WorkflowSubmitRequest, WorkflowSummary } from "../types/api";

const store = useAppStore();
const loading = ref(false);
const submitting = ref(false);
const error = ref("");
const items = ref<WorkflowSummary[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const statusFilter = ref("");
const detailVisible = ref(false);
const detail = ref<WorkflowDetail | null>(null);
const timeRange = ref<[Date, Date] | []>([]);

const form = reactive({
  competitor: "",
  question: "最近有哪些重要产品、价格与风险动态？",
  branches: ["price", "product", "risk"] as SpecialistBranch[],
  top_k: 8,
  max_cards: 5,
  include_snapshot: true,
  include_briefing: true
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const response = await workflowApi.list({ page: page.value, page_size: pageSize.value, status: statusFilter.value || undefined });
    items.value = response.items;
    total.value = response.total;
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}

async function refreshDetail(id: string) {
  try {
    detail.value = await workflowApi.detail(id);
    if (TERMINAL_WORKFLOW_STATUSES.has(detail.value.status)) {
      await load();
    }
    return detail.value.status;
  } catch (reason) {
    ElMessage.error(errorMessage(reason));
    throw reason;
  }
}

const poller = createWorkflowPoller(refreshDetail);
const stopPolling = poller.stop;

function openDetail(id: string) {
  detailVisible.value = true;
  poller.start(id);
}

function openDetailRow(row: WorkflowSummary) { openDetail(row.workflow_id); }

async function submit() {
  if (!form.competitor || !form.branches.length) return ElMessage.warning("请选择竞品和至少一个专业分支");
  submitting.value = true;
  const payload: WorkflowSubmitRequest = {
    competitor: form.competitor,
    analysis_mode: store.agentMode,
    question: form.question || undefined,
    branches: form.branches,
    top_k: form.top_k,
    max_cards: form.max_cards,
    include_snapshot: form.include_snapshot,
    include_briefing: form.include_briefing,
    current_only: true,
    correlation_id: `frontend-${Date.now()}`
  };
  if (timeRange.value.length === 2) {
    payload.start_time = timeRange.value[0].toISOString();
    payload.end_time = timeRange.value[1].toISOString();
  }
  try {
    const response = await workflowApi.submit(payload);
    ElMessage.success(response.deduplicated ? "已命中相同任务" : "Workflow 已提交");
    await load();
    openDetail(response.workflow_id);
  } catch (reason) { ElMessage.error(errorMessage(reason)); }
  finally { submitting.value = false; }
}

async function act(action: "cancel" | "retry") {
  if (!detail.value) return;
  try {
    await workflowApi[action](detail.value.workflow_id);
    ElMessage.success(action === "cancel" ? "取消请求已提交" : "重试请求已提交");
    await refreshDetail(detail.value.workflow_id);
  } catch (reason) { ElMessage.error(errorMessage(reason)); }
}

onMounted(async () => {
  if (!store.competitors.length) await store.loadCompetitors();
  form.competitor = store.enabledCompetitors[0]?.name || "";
  await load();
});
onBeforeUnmount(stopPolling);
</script>

<template>
  <PageHeader title="Workflow" description="提交 Multi-Agent 分析并查看 Price、Product、Risk 三个分支进度" />
  <section class="panel">
    <h3 class="panel-title">提交分析任务</h3>
    <el-form label-width="100px">
      <div class="two-column">
        <div>
          <el-form-item label="竞品">
            <el-select v-model="form.competitor" filterable style="width: 100%">
              <el-option v-for="item in store.enabledCompetitors" :key="item.id" :label="item.name" :value="item.name" />
            </el-select>
          </el-form-item>
          <el-form-item label="分析问题"><el-input v-model="form.question" type="textarea" :rows="3" /></el-form-item>
          <el-form-item label="专业分支">
            <el-checkbox-group v-model="form.branches">
              <el-checkbox value="price">Price</el-checkbox><el-checkbox value="product">Product</el-checkbox><el-checkbox value="risk">Risk</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
        </div>
        <div>
          <el-form-item label="时间范围"><el-date-picker v-model="timeRange" type="datetimerange" style="width: 100%" /></el-form-item>
          <el-form-item label="检索数量"><el-input-number v-model="form.top_k" :min="1" :max="30" /></el-form-item>
          <el-form-item label="最大卡片"><el-input-number v-model="form.max_cards" :min="1" :max="20" /></el-form-item>
          <el-form-item label="生成产物">
            <el-checkbox v-model="form.include_snapshot">能力快照</el-checkbox>
            <el-checkbox v-model="form.include_briefing">竞争简报</el-checkbox>
          </el-form-item>
        </div>
      </div>
      <el-button type="primary" :loading="submitting" @click="submit">提交 Workflow</el-button>
      <el-tag effect="plain" style="margin-left: 12px">执行模式：{{ store.agentMode.toUpperCase() }}</el-tag>
    </el-form>
  </section>
  <section class="panel">
    <div class="filter-bar">
      <h3 class="panel-title" style="margin:0 auto 0 0">任务历史</h3>
      <el-select v-model="statusFilter" clearable placeholder="全部状态" style="width: 160px" @change="page=1; load()">
        <el-option v-for="status in ['queued','running','success','partial_failure','failed','cancelled','timed_out']" :key="status" :label="status" :value="status" />
      </el-select>
      <el-button @click="load">刷新</el-button>
    </div>
    <LoadState :loading="loading" :error="error" :empty="!items.length" @retry="load">
      <el-table :data="items" @row-click="openDetailRow">
        <el-table-column prop="competitor" label="竞品" width="130" />
        <el-table-column prop="analysis_mode" label="模式" width="90" />
        <el-table-column label="状态" width="120"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
        <el-table-column label="进度" width="180"><template #default="scope"><el-progress :percentage="scope.row.progress" /></template></el-table-column>
        <el-table-column prop="attempt_count" label="尝试" width="80" />
        <el-table-column prop="submitted_at" label="提交时间" min-width="190" />
        <el-table-column prop="correlation_id" label="Correlation ID" min-width="190" show-overflow-tooltip />
      </el-table>
      <div class="pagination"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="load" /></div>
    </LoadState>
  </section>

  <el-drawer v-model="detailVisible" title="Workflow 详情" size="620px" @closed="stopPolling">
    <LoadState :loading="!detail">
      <template v-if="detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="竞品">{{ detail.competitor }}</el-descriptions-item>
          <el-descriptions-item label="模式">{{ detail.analysis_mode.toUpperCase() }}</el-descriptions-item>
          <el-descriptions-item label="状态"><StatusTag :status="detail.status" /></el-descriptions-item>
          <el-descriptions-item label="进度">{{ detail.progress }}%</el-descriptions-item>
          <el-descriptions-item label="尝试">{{ detail.attempt_count }}/{{ detail.max_attempts }}</el-descriptions-item>
          <el-descriptions-item label="Snapshot">{{ detail.snapshot_id || "—" }}</el-descriptions-item>
          <el-descriptions-item label="Briefing">{{ detail.briefing_id || "—" }}</el-descriptions-item>
        </el-descriptions>
        <h3>专业分支</h3>
        <el-table :data="detail.branches">
          <el-table-column prop="branch" label="分支" width="100" />
          <el-table-column label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
          <el-table-column prop="duration_ms" label="耗时(ms)" width="110" />
          <el-table-column prop="trace_id" label="Trace" min-width="150" show-overflow-tooltip />
          <el-table-column label="Cards" width="70"><template #default="scope">{{ scope.row.card_ids.length }}</template></el-table-column>
        </el-table>
        <el-alert v-if="detail.last_error" type="error" :closable="false" :title="JSON.stringify(detail.last_error)" style="margin-top:16px" />
        <div style="margin-top:16px; display:flex; gap:8px">
          <el-button v-if="['queued','running','cancelling'].includes(detail.status)" type="danger" @click="act('cancel')">取消</el-button>
          <el-button v-if="['failed','partial_failure','timed_out','cancelled'].includes(detail.status)" type="primary" @click="act('retry')">重试失败分支</el-button>
        </div>
      </template>
    </LoadState>
  </el-drawer>
</template>
