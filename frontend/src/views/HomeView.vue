<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { cardApi, comparisonApi, snapshotApi, workflowApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import AbilityStar from "../components/charts/AbilityStar.vue";
import CapabilityMatrix from "../components/charts/CapabilityMatrix.vue";
import EventStream from "../components/charts/EventStream.vue";
import SignalFieldHero from "../components/SignalFieldHero.vue";
import LoadState from "../components/LoadState.vue";
import type { CardDetail, CardSummary, ComparisonResponse, SnapshotDetail, SnapshotSummary, WorkflowSummary } from "../types/api";

const store = useAppStore();
const loading = ref(false);
const error = ref("");
const cards = ref<CardSummary[]>([]);
const snapshots = ref<SnapshotSummary[]>([]);
const latestWorkflow = ref<WorkflowSummary | null>(null);
const comparison = ref<ComparisonResponse | null>(null);
const selectedTarget = ref("");
const selectedEventTarget = ref("");
const selectedSnapshot = ref<SnapshotDetail | null>(null);
const cardDrawer = ref(false);
const cardDetail = ref<CardDetail | null>(null);
const heroNarrative = ref<HTMLElement | null>(null);
const heroProgress = ref(0);
let scrollFrame = 0;

const targets = computed(() => Array.from(new Set(snapshots.value.map((item) => item.competitor))));
const visibleEvents = computed(() => cards.value.filter((item) =>
  !selectedEventTarget.value || item.competitor === selectedEventTarget.value
));
const importantCards = computed(() => [...cards.value].sort((a, b) => b.priority_score - a.priority_score).slice(0, 6));
const products = computed(() => comparison.value?.matrix.products.map((item) => item.product) || []);
const referenceOrder = computed(() => [...(comparison.value?.matrix.products || [])]
  .filter((item) => item.weighted_total_score !== null)
  .sort((a, b) => Number(b.weighted_total_score) - Number(a.weighted_total_score)));
const coverageAverage = computed(() => snapshots.value.length
  ? Math.round(snapshots.value.reduce((sum, item) => sum + item.coverage_ratio, 0) / snapshots.value.length * 100)
  : 0);

function clamp(value: number, min = 0, max = 1) {
  return Math.min(max, Math.max(min, value));
}

const gridProgress = computed(() => clamp((heroProgress.value - 0.72) / 0.18));
const brandFade = computed(() => clamp((heroProgress.value - 0.34) / 0.18));
const controlFade = computed(() => clamp((heroProgress.value - 0.38) / 0.24));
const handoffProgress = computed(() => clamp((heroProgress.value - 0.88) / 0.11));
const focusCopyProgress = computed(() => {
  const entering = clamp((heroProgress.value - 0.43) / 0.11);
  const leaving = clamp((heroProgress.value - 0.82) / 0.09);
  return entering * (1 - leaving);
});
const heroStyle = computed(() => ({
  "--hero-progress": heroProgress.value,
  "--hero-transition": gridProgress.value,
  "--hero-grid-opacity": 0.035 + gridProgress.value * 0.68,
  backgroundColor: "#f4f1ea",
  color: "#15171b",
}));
const brandStyle = computed(() => ({
  opacity: 1 - brandFade.value,
  transform: `translateY(${brandFade.value * 92}px) scale(${1 + brandFade.value * 0.055})`,
}));
const controlStyle = computed(() => ({
  opacity: 1 - controlFade.value,
  transform: `translateY(${-controlFade.value * 20}px)`,
}));
const handoffStyle = computed(() => ({
  opacity: handoffProgress.value,
  transform: `translateY(${(1 - handoffProgress.value) * 52}px)`,
}));
const focusCopyStyle = computed(() => ({
  opacity: focusCopyProgress.value,
  transform: `translate(-50%, calc(-50% + ${(1 - focusCopyProgress.value) * 20}px))`,
}));

function updateHeroProgress() {
  scrollFrame = 0;
  const element = heroNarrative.value;
  if (!element) return;
  const rect = element.getBoundingClientRect();
  const travel = Math.max(1, rect.height - window.innerHeight);
  heroProgress.value = clamp(-rect.top / travel);
  window.dispatchEvent(new CustomEvent("coderadar:hero-progress", { detail: heroProgress.value }));
}

function requestHeroProgress() {
  if (!scrollFrame) scrollFrame = window.requestAnimationFrame(updateHeroProgress);
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await Promise.all([store.refreshStatus(), store.loadCompetitors()]);
    const [cardPage, snapshotPage, workflowPage] = await Promise.all([
      cardApi.list({ page: 1, page_size: 100, sort_by: "created_at", order: "desc" }),
      snapshotApi.list({ page: 1, page_size: 100 }),
      workflowApi.list({ page: 1, page_size: 1 })
    ]);
    cards.value = cardPage.items;
    snapshots.value = snapshotPage.items;
    latestWorkflow.value = workflowPage.items[0] || null;
    try { comparison.value = await comparisonApi.latest(); } catch { comparison.value = null; }
    if (!selectedTarget.value) selectedTarget.value = snapshots.value[0]?.competitor || "";
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    loading.value = false;
  }
}

async function loadSelectedSnapshot() {
  const item = snapshots.value.find((snapshot) => snapshot.competitor === selectedTarget.value);
  selectedSnapshot.value = item ? await snapshotApi.detail(item.snapshot_id) : null;
}

async function openCard(card: CardSummary) {
  cardDrawer.value = true;
  cardDetail.value = null;
  try { cardDetail.value = await cardApi.detail(card.card_id); } catch { cardDetail.value = null; }
}

watch(selectedTarget, () => void loadSelectedSnapshot());
onMounted(async () => {
  window.addEventListener("scroll", requestHeroProgress, { passive: true });
  window.addEventListener("resize", requestHeroProgress, { passive: true });
  updateHeroProgress();
  if (window.location.hash === "#signal-to-evidence") {
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      document.getElementById("signal-to-evidence")?.scrollIntoView({ block: "start" });
      window.requestAnimationFrame(updateHeroProgress);
    }));
  }
  await load();
  await loadSelectedSnapshot();
});
onBeforeUnmount(() => {
  window.cancelAnimationFrame(scrollFrame);
  window.removeEventListener("scroll", requestHeroProgress);
  window.removeEventListener("resize", requestHeroProgress);
});
</script>

<template>
  <div class="home-page">
    <section ref="heroNarrative" class="hero-narrative" :style="heroStyle">
      <div class="hero-stage">
        <SignalFieldHero :progress="heroProgress" />
        <div class="hero__grid" aria-hidden="true" />
        <div class="hero-top-rail" :style="controlStyle">
          <div><span>LIVE MODEL SIGNALS</span><small>PUBLIC EVIDENCE · CONTINUOUS OBSERVATION</small></div>
          <div class="hero-top-rail__actions">
            <button type="button" class="hero-top-rail__secondary" @click="$router.push('/ask')">随便问问 <span>↗</span></button>
            <button type="button" class="hero-top-rail__secondary" @click="$router.push('/analysis')">生成深度报告 <span>↗</span></button>
          </div>
        </div>
        <div class="hero-brand-lockup" :style="brandStyle">
          <h1>CodeRadar</h1>
          <div class="hero-wordmark-meta">
            <span>MODEL INTELLIGENCE OBSERVATORY</span>
            <span>SCROLL TO OBSERVE ↓</span>
          </div>
        </div>
        <div class="hero-focus-copy" :style="focusCopyStyle" aria-hidden="true">
          <span class="hero-focus-copy__eyebrow">OBSERVATION / 01</span>
          <div class="hero-focus-copy__title">
            <strong>SIGNAL</strong>
            <i />
            <strong>EVIDENCE</strong>
          </div>
          <small>轨道正在展开为可追溯坐标</small>
        </div>
        <div class="hero__service">
          <span class="status-dot" :class="{ online: store.apiOnline }" />
          <span>{{ store.apiOnline ? "情报服务在线" : "情报服务离线" }}</span>
          <span class="hero__service-divider" />
          <span>{{ store.ready?.indexed_chunks ?? 0 }} 个证据切片</span>
        </div>
      </div>
      <span id="signal-to-evidence" class="hero-transition-anchor" aria-hidden="true" />
      <div class="hero-handoff" :style="handoffStyle">
        <div class="hero-handoff__heading">
          <span>FROM SIGNAL TO EVIDENCE</span>
          <h2>每一次变化，都落入可追溯的坐标。</h2>
          <p>观测轨道已展开为数据网格，真实摘要随滚动进入。</p>
        </div>
        <div class="metric-strip hero-metric-strip" aria-label="系统摘要">
          <div><span>分析对象</span><strong>{{ store.enabledCompetitors.length }}</strong><small>MODELS</small></div>
          <div><span>近期情报</span><strong>{{ cards.length }}</strong><small>SIGNALS</small></div>
          <div><span>平均覆盖</span><strong>{{ coverageAverage }}%</strong><small>COVERAGE</small></div>
          <div><span>最新分析</span><strong class="metric-strip__status">{{ latestWorkflow?.status || "暂无" }}</strong><small>WORKFLOW</small></div>
        </div>
      </div>
    </section>

    <div class="home-data">
      <LoadState :loading="loading" :error="error" @retry="load">
        <section class="content-section event-section">
          <div class="section-heading">
            <div><span class="section-index">01</span><h2>趋势事件流</h2><p>追踪各模型重要更新与风险动态</p></div>
            <div class="filter-bar compact">
              <el-select v-model="selectedEventTarget" clearable placeholder="全部模型" style="width:150px">
                <el-option v-for="item in targets" :key="item" :label="item" :value="item" />
              </el-select>
            </div>
          </div>
          <EventStream :cards="visibleEvents" :all-cards="cards" @select="openCard" />
        </section>

        <div class="visual-observatory">
          <section class="content-section star-section">
            <div class="section-heading">
              <div><span class="section-index">02</span><h2>最新能力星图</h2><p>分数、置信度与证据量的组合视图</p></div>
              <el-tag v-if="selectedSnapshot?.source_kind === 'product_definition'" type="warning">产品设计基线</el-tag>
            </div>
            <AbilityStar :snapshot="selectedSnapshot?.snapshot || null" />
            <div v-if="selectedSnapshot" class="metric-row">
              <span>总分 <strong>{{ selectedSnapshot.snapshot.total_score.toFixed(1) }}</strong></span>
              <span>覆盖率 <strong>{{ Math.round(selectedSnapshot.snapshot.coverage_ratio * 100) }}%</strong></span>
              <span>置信度 <strong>{{ Math.round(selectedSnapshot.snapshot.overall_confidence * 100) }}%</strong></span>
            </div>
          </section>

          <aside class="content-section ranking-section">
            <div class="section-heading"><div><span class="section-index">03</span><h2>参考排序</h2><p>基于最新可比较快照</p></div></div>
            <p v-if="comparison && !comparison.official_ranking_ready" class="ranking-notice">当前数据覆盖不足，排序仅供观察。</p>
            <el-empty v-if="!referenceOrder.length" description="暂无可比较结果" />
            <ol v-else class="ranking-list">
              <li v-for="(item, index) in referenceOrder" :key="item.product">
                <span class="ranking-index">{{ String(index + 1).padStart(2, '0') }}</span>
                <div><strong>{{ item.product }}</strong><small>{{ item.rank_eligible ? "覆盖充分" : item.ranking_reason }}</small></div>
                <b>{{ item.weighted_total_score?.toFixed(1) }}</b>
              </li>
            </ol>
          </aside>
        </div>

        <section class="content-section">
          <div class="section-heading"><div><span class="section-index">04</span><h2>最新能力矩阵</h2><p>N/A 表示证据不足，不参与颜色映射与平均值</p></div></div>
          <CapabilityMatrix v-if="comparison" :rows="comparison.matrix.rows" :products="products" />
          <el-empty v-else description="暂无能力矩阵" />
        </section>

        <section class="content-section changes-section">
          <div class="section-heading"><div><span class="section-index">05</span><h2>最新关键变化</h2><p>按情报优先级排列，点击查看原始证据</p></div></div>
          <div class="signal-list">
            <article v-for="card in importantCards" :key="card.card_id" @click="openCard(card)">
              <div class="signal-list__index">{{ card.competitor.slice(0, 2).toUpperCase() }}</div>
              <div><span>{{ card.competitor }} · {{ card.event_type }}</span><h3>{{ card.event_title }}</h3><p>{{ card.summary }}</p></div>
              <div class="signal-list__meta"><strong>{{ card.priority_score }}</strong><small>PRIORITY</small><span>{{ card.evidence_count }} 条证据 · {{ Math.round(card.confidence_score * 100) }}%</span></div>
            </article>
          </div>
        </section>
      </LoadState>
    </div>

    <el-drawer v-model="cardDrawer" title="情报与证据" size="560px">
      <el-skeleton v-if="!cardDetail" :rows="5" animated />
      <template v-else>
        <h2>{{ cardDetail.card.event_title }}</h2><p>{{ cardDetail.card.summary }}</p>
        <el-alert v-if="cardDetail.card.review_required" type="warning" :closable="false" title="该情报需要人工复核" />
        <div v-for="(evidence, index) in cardDetail.evidence_links" :key="evidence.chunk_id" class="evidence-card">
          <strong>[{{ index + 1 }}] {{ evidence.title }}</strong>
          <p>{{ evidence.quote || "暂无引用片段" }}</p>
          <small>{{ evidence.evidence_level }} · {{ evidence.source_type }} · {{ evidence.publish_time || "时间未知" }}</small>
          <a :href="evidence.url" target="_blank" rel="noopener noreferrer">打开原始来源</a>
        </div>
      </template>
    </el-drawer>
  </div>
</template>
