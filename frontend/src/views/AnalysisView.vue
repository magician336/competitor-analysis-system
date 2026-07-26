<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { useRoute, useRouter } from "vue-router";
import { workflowApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import LoadState from "../components/LoadState.vue";
import StatusTag from "../components/StatusTag.vue";
import { ANALYSIS_TIME_OPTIONS, analysisTimeLabel, buildAnalysisWindow, presetFromCorrelationId, workflowCorrelationId } from "../utils/analysisTime";
import type { AgentMode, AnalysisTimePreset, AnalysisWizardStep, SpecialistBranch, WorkflowSummary } from "../types/api";

const route = useRoute();
const router = useRouter();
const store = useAppStore();
const activeTab = ref(route.query.tab === "history" ? "history" : "create");
const currentStep = ref<AnalysisWizardStep>("mode");
const submitting = ref(false);
const loading = ref(false);
const openingWorkflow = ref("");
const error = ref("");
const items = ref<WorkflowSummary[]>([]);
const page = ref(1);
const pageSize = ref(10);
const total = ref(0);
const filters = reactive({ competitor: "", status: "", analysisMode: "" });
const presets = [
  "总结近期最重要的产品能力变化",
  "分析近期 Agent 与上下文能力的发展趋势",
  "梳理近期价格策略、产品更新与潜在风险",
  "评估当前七维能力表现及证据覆盖情况",
];
const form = reactive({
  competitor: "",
  question: presets[0],
  timePreset: "30d" as AnalysisTimePreset,
  branches: ["product", "price", "risk"] as SpecialistBranch[],
  mode: store.agentMode as AgentMode,
  topK: 8,
  maxCards: 10,
  includeSnapshot: true,
  includeBriefing: true,
});
const modeOptions: Array<{ value: AgentMode; title: string; code: string; subtitle: string; note: string }> = [
  { value: "rules", title: "快速分析", code: "FAST", subtitle: "确定性规则与结构化证据", note: "执行稳定，适合快速查看趋势结构" },
  { value: "hybrid", title: "AI 辅助分析", code: "ASSIST", subtitle: "证据与规则约束下使用 AI", note: "模型异常时自动回退，兼顾效率与表达" },
  { value: "llm", title: "深度 AI 分析", code: "DEEP", subtitle: "严格依赖模型理解与综合", note: "适合完整分析，模型异常时任务会失败" },
];
const branchOptions = [
  { value: "product", label: "产品能力", code: "P", help: "功能发布、Agent、IDE 与模型扩展" },
  { value: "price", label: "价格动态", code: "¥", help: "套餐、额度、计费与成本变化" },
  { value: "risk", label: "风险信号", code: "R", help: "安全、稳定性、用户反馈与争议信号" },
] as const;
const wizardSteps: Array<{ value: AnalysisWizardStep; index: string; label: string }> = [
  { value: "mode", index: "01", label: "分析方式" },
  { value: "scope", index: "02", label: "分析范围" },
  { value: "task", index: "03", label: "分析任务" },
  { value: "review", index: "04", label: "确认提交" },
];
const stepIndex = computed(() => wizardSteps.findIndex((step) => step.value === currentStep.value));
const selectedMode = computed(() => modeOptions.find((item) => item.value === form.mode) || modeOptions[0]);
const selectedTime = computed(() => ANALYSIS_TIME_OPTIONS.find((item) => item.value === form.timePreset));
const selectedBranches = computed(() => branchOptions.filter((item) => form.branches.includes(item.value)));
const canSubmit = computed(() => Boolean(form.competitor && form.question.trim() && form.branches.length));

function selectMode(mode: AgentMode) {
  form.mode = mode;
  store.setAgentMode(mode);
}

function toggleBranch(branch: SpecialistBranch) {
  if (form.branches.includes(branch)) {
    if (form.branches.length === 1) return;
    form.branches = form.branches.filter((item) => item !== branch);
  } else form.branches = [...form.branches, branch];
}

function nextStep() {
  if (currentStep.value === "scope" && !form.competitor) {
    ElMessage.warning("请先选择分析对象");
    return;
  }
  if (currentStep.value === "task" && (!form.question.trim() || !form.branches.length)) {
    ElMessage.warning("请填写分析问题并至少选择一个分析方面");
    return;
  }
  const next = wizardSteps[stepIndex.value + 1];
  if (next) currentStep.value = next.value;
}

function previousStep() {
  const previous = wizardSteps[stepIndex.value - 1];
  if (previous) currentStep.value = previous.value;
}

function goToCompletedStep(step: AnalysisWizardStep) {
  const target = wizardSteps.findIndex((item) => item.value === step);
  if (target <= stepIndex.value) currentStep.value = step;
}

async function submit() {
  if (!canSubmit.value || submitting.value) return;
  submitting.value = true;
  try {
    store.setAgentMode(form.mode);
    const analysisWindow = buildAnalysisWindow(form.timePreset);
    const response = await workflowApi.submit({
      competitor: form.competitor,
      analysis_mode: form.mode,
      question: form.question.trim(),
      branches: form.branches,
      ...analysisWindow,
      top_k: form.topK,
      max_cards: form.maxCards,
      include_snapshot: form.includeSnapshot,
      include_briefing: form.includeBriefing,
      current_only: false,
      correlation_id: workflowCorrelationId(form.timePreset),
    });
    ElMessage.success(response.deduplicated ? "已打开相同报告任务" : "深度报告任务已提交");
    await router.push({ path: `/analysis/${response.workflow_id}`, query: { range: form.timePreset } });
  } catch (reason) { ElMessage.error(errorMessage(reason)); }
  finally { submitting.value = false; }
}

async function loadHistory() {
  loading.value = true;
  error.value = "";
  try {
    const result = await workflowApi.list({
      page: page.value,
      page_size: pageSize.value,
      competitor: filters.competitor,
      status: filters.status,
      analysis_mode: filters.analysisMode,
    });
    items.value = result.items;
    total.value = result.total;
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}

async function openHistory(row: WorkflowSummary) {
  if (openingWorkflow.value) return;
  openingWorkflow.value = row.workflow_id;
  const preset = presetFromCorrelationId(row.correlation_id);
  const query = preset ? { range: preset } : {};
  try {
    const detail = await workflowApi.detail(row.workflow_id);
    const path = detail.briefing_id && ["success", "partial_failure"].includes(detail.status)
      ? `/analysis/${row.workflow_id}/report`
      : `/analysis/${row.workflow_id}`;
    await router.push({ path, query });
  } catch {
    await router.push({ path: `/analysis/${row.workflow_id}`, query });
  } finally { openingWorkflow.value = ""; }
}

function historyTimeLabel(row: WorkflowSummary) {
  return analysisTimeLabel(presetFromCorrelationId(row.correlation_id));
}

watch(activeTab, async (value) => {
  await router.replace({ query: value === "history" ? { tab: "history" } : {} });
  if (value === "history") await loadHistory();
});
onMounted(async () => {
  if (!store.competitors.length) await store.loadCompetitors();
  if (activeTab.value === "history") await loadHistory();
});
</script>

<template>
  <div class="analysis-page route-grid" :data-analysis-mode="form.mode">
    <div class="analysis-page__glow" aria-hidden="true" />
    <div class="analysis-frame page-wide">
      <header class="page-intro analysis-intro primary-page-intro">
        <span class="module-kicker">MULTI-AGENT ANALYSIS</span>
        <h1>深度报告</h1>
        <p>从一种分析方式开始，再定义范围与问题。专业 Agent 将并行工作并生成 Markdown 趋势简报。</p>
      </header>

      <el-tabs v-model="activeTab" class="primary-tabs analysis-tabs">
        <el-tab-pane label="生成报告" name="create">
          <section class="analysis-wizard">
            <nav class="wizard-progress" aria-label="分析步骤">
              <button
                v-for="(step, index) in wizardSteps"
                :key="step.value"
                type="button"
                :class="{ active: currentStep === step.value, complete: index < stepIndex }"
                :disabled="index > stepIndex"
                @click="goToCompletedStep(step.value)"
              ><span>{{ step.index }}</span><strong>{{ step.label }}</strong></button>
            </nav>

            <div class="wizard-stage" :data-wizard-step="currentStep">
              <transition name="wizard-step" mode="out-in">
                <section v-if="currentStep === 'mode'" key="mode" class="wizard-pane wizard-pane--mode">
                  <header><span>01 / MODE</span><h2>选择这次分析的深度</h2></header>
                  <div class="mode-grid" role="radiogroup" aria-label="分析方式">
                    <button
                      v-for="item in modeOptions"
                      :key="item.value"
                      type="button"
                      class="mode-option observable-module"
                      :class="{ active: form.mode === item.value }"
                      :data-mode="item.value"
                      :data-test="`analysis-mode-${item.value}`"
                      :aria-pressed="form.mode === item.value"
                      @click="selectMode(item.value)"
                    >
                      <span class="module-corners" aria-hidden="true" />
                      <small>{{ item.code }}</small><strong>{{ item.title }}</strong><p>{{ item.subtitle }}</p><em>{{ item.note }}</em>
                    </button>
                  </div>
                </section>

                <section v-else-if="currentStep === 'scope'" key="scope" class="wizard-pane">
                  <header><span>02 / SCOPE</span><h2>确定观察对象与时间窗口</h2></header>
                  <div class="scope-console observable-module">
                    <label><span>分析对象</span><strong>{{ form.competitor || "尚未选择" }}</strong></label>
                    <el-select v-model="form.competitor" placeholder="选择模型或工具" size="large">
                      <el-option v-for="item in store.enabledCompetitors" :key="item.id" :label="item.name" :value="item.name" />
                    </el-select>
                    <div class="time-choice" role="radiogroup" aria-label="时间范围">
                      <button v-for="item in ANALYSIS_TIME_OPTIONS" :key="item.value" type="button" :class="{ active: form.timePreset === item.value }" @click="form.timePreset = item.value">{{ item.label }}</button>
                    </div>
                  </div>
                </section>

                <section v-else-if="currentStep === 'task'" key="task" class="wizard-pane">
                  <header><span>03 / TASK</span><h2>告诉 Agent 需要回答什么</h2><p>聚焦具体问题，按需选择分析视角。</p></header>
                  <div class="preset-list">
                    <button v-for="item in presets" :key="item" type="button" :class="{ active: form.question === item }" @click="form.question = item">{{ item }}</button>
                  </div>
                  <el-input v-model="form.question" type="textarea" :rows="4" maxlength="1000" show-word-limit placeholder="输入自定义分析问题" />
                  <div class="branch-grid" role="group" aria-label="分析方面">
                    <button v-for="item in branchOptions" :key="item.value" type="button" :class="{ active: form.branches.includes(item.value) }" @click="toggleBranch(item.value)">
                      <b>{{ item.code }}</b><span><strong>{{ item.label }}</strong><small>{{ item.help }}</small></span>
                    </button>
                  </div>
                  <el-collapse class="advanced-options">
                    <el-collapse-item title="高级参数" name="advanced">
                      <div class="advanced-grid">
                        <label class="advanced-number-card">
                          <span><strong>检索证据数</strong><small>控制每个分析分支可引用的证据上限</small></span>
                          <el-input-number v-model="form.topK" :min="1" :max="30" />
                        </label>
                        <label class="advanced-number-card">
                          <span><strong>最大情报卡片</strong><small>限制最终进入报告的结构化发现数量</small></span>
                          <el-input-number v-model="form.maxCards" :min="1" :max="30" />
                        </label>
                        <div class="advanced-output-grid">
                          <el-checkbox v-model="form.includeSnapshot" class="advanced-toggle">
                            <span><strong>生成能力快照</strong><small>汇总 D1–D7 评分、覆盖率与证据数量</small></span>
                          </el-checkbox>
                          <el-checkbox v-model="form.includeBriefing" class="advanced-toggle">
                            <span><strong>生成 Markdown 简报</strong><small>输出可阅读、可下载的完整趋势报告</small></span>
                          </el-checkbox>
                        </div>
                      </div>
                    </el-collapse-item>
                  </el-collapse>
                </section>

                <section v-else key="review" class="wizard-pane wizard-pane--review">
                  <header><span>04 / REVIEW</span><h2>确认分析任务</h2><p>提交后将汇集相关证据，生成一份可追溯、可阅读的分析报告。</p></header>
                  <div class="review-board observable-module">
                    <div><small>分析方式</small><strong>{{ selectedMode.title }}</strong><span>{{ selectedMode.subtitle }}</span></div>
                    <div><small>分析对象</small><strong>{{ form.competitor }}</strong><span>{{ selectedTime?.label }}</span></div>
                    <div class="review-board__wide"><small>分析问题</small><strong>{{ form.question }}</strong></div>
                    <div class="review-board__wide"><small>专业分支</small><span class="review-tags"><b v-for="item in selectedBranches" :key="item.value">{{ item.label }}</b></span></div>
                    <div><small>证据上限</small><strong>{{ form.topK }}</strong><span>TOP K</span></div>
                    <div><small>预期产物</small><strong>{{ form.includeBriefing ? "Markdown" : "结构化结果" }}</strong><span>{{ form.includeSnapshot ? "包含能力快照" : "不生成快照" }}</span></div>
                  </div>
                </section>
              </transition>
            </div>

            <footer class="wizard-actions">
              <button v-if="stepIndex > 0" type="button" class="wizard-back" data-test="wizard-back" @click="previousStep">← 上一步</button><span v-else />
              <el-button v-if="currentStep !== 'review'" type="primary" size="large" data-test="wizard-next" @click="nextStep">继续 <span>→</span></el-button>
              <el-button v-else type="primary" size="large" data-test="wizard-submit" :loading="submitting" :disabled="!canSubmit" @click="submit">生成深度报告 <span>↗</span></el-button>
            </footer>
          </section>
        </el-tab-pane>

        <el-tab-pane label="报告记录" name="history">
          <section class="history-section content-section">
            <div class="filter-bar">
              <el-select v-model="filters.competitor" clearable placeholder="全部分析对象" style="width:170px" @change="page=1; loadHistory()"><el-option v-for="item in store.competitors" :key="item.id" :label="item.name" :value="item.name" /></el-select>
              <el-select v-model="filters.status" clearable placeholder="全部状态" style="width:150px" @change="page=1; loadHistory()"><el-option v-for="item in ['queued','running','success','partial_failure','failed','cancelled','timed_out']" :key="item" :label="item" :value="item" /></el-select>
              <el-select v-model="filters.analysisMode" clearable placeholder="全部模式" style="width:150px" @change="page=1; loadHistory()"><el-option v-for="item in modeOptions" :key="item.value" :label="item.title" :value="item.value" /></el-select>
              <el-button @click="loadHistory">刷新</el-button>
            </div>
            <LoadState :loading="loading" :error="error" :empty="!items.length" @retry="loadHistory">
              <el-table :data="items" class="history-table" @row-click="openHistory">
                <el-table-column prop="competitor" label="分析对象" min-width="150" />
                <el-table-column label="分析方式" min-width="130"><template #default="scope">{{ modeOptions.find(item => item.value === scope.row.analysis_mode)?.title || scope.row.analysis_mode }}</template></el-table-column>
                <el-table-column label="时间范围" min-width="110"><template #default="scope">{{ historyTimeLabel(scope.row) }}</template></el-table-column>
                <el-table-column label="状态" min-width="130"><template #default="scope"><StatusTag :status="scope.row.status" /></template></el-table-column>
                <el-table-column prop="submitted_at" label="提交时间" width="260" />
                <el-table-column label="" width="120"><template #default="scope"><el-button link type="primary" :loading="openingWorkflow === scope.row.workflow_id" @click.stop="openHistory(scope.row)">打开报告</el-button></template></el-table-column>
              </el-table>
              <div class="pagination"><el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="loadHistory" /></div>
            </LoadState>
          </section>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>
