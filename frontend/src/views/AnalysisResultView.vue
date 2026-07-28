<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Loading } from "@element-plus/icons-vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { workflowApi } from "../api/services";
import { errorMessage } from "../api/client";
import LoadState from "../components/LoadState.vue";
import StatusTag from "../components/StatusTag.vue";
import { analysisTimeLabel, isAnalysisTimePreset } from "../utils/analysisTime";
import type { WorkflowDetail } from "../types/api";

const route = useRoute();
const router = useRouter();
const workflowId = String(route.params.workflowId);
const loading = ref(true);
const redirecting = ref(false);
const error = ref("");
const detail = ref<WorkflowDetail | null>(null);
let timer: number | null = null;
const terminal = new Set(["success", "partial_failure", "failed", "cancelled", "timed_out"]);
const modeLabels = { rules: "快速分析", hybrid: "AI 辅助分析", llm: "深度 AI 分析" } as const;
const branchLabels = { product: "产品能力", price: "价格动态", risk: "风险信号" } as const;
const timePreset = computed(() => isAnalysisTimePreset(route.query.range) ? route.query.range : null);
const timeRangeLabel = computed(() => analysisTimeLabel(timePreset.value));
const progressStage = computed(() => {
  const progress = detail.value?.progress || 0;
  if (terminal.has(detail.value?.status || "")) return 5;
  if (progress >= 95) return 4;
  if (progress >= 90) return 3;
  if (progress >= 10) return 2;
  return 1;
});

async function openReport() {
  if (!detail.value?.briefing_id || redirecting.value) return;
  redirecting.value = true;
  await router.replace({ path: `/analysis/${workflowId}/report`, query: timePreset.value ? { range: timePreset.value } : {} });
}

async function loadStatus() {
  try {
    detail.value = await workflowApi.detail(workflowId);
    error.value = "";
    if (terminal.has(detail.value.status)) {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      if (detail.value.briefing_id && ["success", "partial_failure"].includes(detail.value.status)) await openReport();
    } else timer = window.setTimeout(() => void loadStatus(), 1500);
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}

async function act(action: "cancel" | "retry") {
  try {
    if (action === "cancel") await workflowApi.cancel(workflowId);
    else await workflowApi.retry(workflowId);
    ElMessage.success(action === "cancel" ? "已提交取消请求" : "已重新排队失败分支");
    await loadStatus();
  } catch (reason) { ElMessage.error(errorMessage(reason)); }
}

onMounted(loadStatus);
onBeforeUnmount(() => { if (timer !== null) window.clearTimeout(timer); });
</script>

<template>
  <div class="analysis-result route-grid" :data-analysis-mode="detail?.analysis_mode || 'rules'">
    <div class="analysis-page__glow" aria-hidden="true" />
    <div class="result-frame page-wide">
      <div class="result-toolbar">
        <el-button @click="router.push('/analysis?tab=history')">返回报告记录</el-button>
        <div v-if="detail" class="result-toolbar__meta"><strong>{{ detail.competitor }}</strong><StatusTag :status="detail.status" /><span>{{ modeLabels[detail.analysis_mode] }}</span><el-tag size="small" effect="plain">{{ timeRangeLabel }}</el-tag></div>
        <div class="result-toolbar__actions">
          <el-button v-if="detail && ['queued','running','cancelling'].includes(detail.status)" type="danger" plain @click="act('cancel')">取消分析</el-button>
          <el-button v-if="detail && ['failed','partial_failure','timed_out','cancelled'].includes(detail.status)" type="primary" plain @click="act('retry')">重试失败部分</el-button>
        </div>
      </div>

      <LoadState :loading="loading || redirecting" :error="error" @retry="loadStatus">
        <template v-if="detail">
          <section v-if="!terminal.has(detail.status)" class="execution-panel observable-module">
            <div class="execution-heading">
              <el-icon class="is-loading" :size="28"><Loading /></el-icon>
              <div><span>WORKFLOW IN PROGRESS</span><h1>深度报告正在生成</h1><p>专业分支并行处理，当前进度 {{ detail.progress }}%</p></div>
            </div>
            <el-progress :percentage="detail.progress" :stroke-width="8" />
            <ol class="execution-steps">
              <li :class="{ done: progressStage > 1, active: progressStage === 1 }">正在检索相关证据</li>
              <li :class="{ done: progressStage > 2, active: progressStage === 2 }">并行调用专业 Agent</li>
              <li :class="{ done: progressStage > 3, active: progressStage === 3 }">汇总能力快照</li>
              <li :class="{ done: progressStage > 4, active: progressStage === 4 }">生成 Markdown 简报</li>
              <li :class="{ done: progressStage === 5 }">报告完成</li>
            </ol>
            <div class="branch-status-row">
              <div v-for="branch in detail.branches" :key="branch.branch"><strong>{{ branchLabels[branch.branch] }}</strong><StatusTag :status="branch.status" /></div>
            </div>
          </section>

          <section v-else class="terminal-result observable-module">
            <span>WORKFLOW / {{ detail.status.toUpperCase() }}</span>
            <h1>{{ detail.briefing_id ? "正在打开 Markdown 简报" : "本次分析没有可阅读的简报" }}</h1>
            <p v-if="detail.briefing_id">分析产物已经生成，页面将自动进入全文阅读。</p>
            <p v-else>任务状态为 {{ detail.status }}。可查看运行详情，或返回报告记录重新提交任务。</p>
            <el-button v-if="detail.briefing_id" type="primary" @click="openReport">打开 Markdown</el-button>
          </section>

          <el-collapse class="advanced-details">
            <el-collapse-item title="高级运行详情" name="details">
              <el-descriptions :column="2" border>
                <el-descriptions-item label="Workflow ID"><span class="mono">{{ detail.workflow_id }}</span></el-descriptions-item>
                <el-descriptions-item label="执行模式">{{ detail.analysis_mode }}</el-descriptions-item>
                <el-descriptions-item label="尝试次数">{{ detail.attempt_count }}/{{ detail.max_attempts }}</el-descriptions-item>
                <el-descriptions-item label="Snapshot ID"><span class="mono">{{ detail.snapshot_id || '—' }}</span></el-descriptions-item>
              </el-descriptions>
              <el-table :data="detail.branches" style="margin-top:16px">
                <el-table-column prop="branch" label="分支" width="100" />
                <el-table-column label="状态" width="110"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
                <el-table-column prop="duration_ms" label="耗时(ms)" width="120" />
                <el-table-column label="ReAct" width="130">
                  <template #default="scope">
                    <el-tag v-if="scope.row.react_used" type="success" size="small">
                      已执行 · {{ scope.row.react_iterations }} 轮
                    </el-tag>
                    <span v-else>—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="tool_call_count" label="工具调用" width="100" />
                <el-table-column prop="trace_id" label="Trace" min-width="180" show-overflow-tooltip />
                <el-table-column label="Cards" width="80"><template #default="scope">{{ scope.row.card_ids.length }}</template></el-table-column>
              </el-table>
              <el-alert v-if="detail.last_error" type="error" :closable="false" :title="JSON.stringify(detail.last_error)" style="margin-top:16px" />
            </el-collapse-item>
          </el-collapse>
        </template>
      </LoadState>
    </div>
  </div>
</template>
