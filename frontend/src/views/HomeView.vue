<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { cardApi, snapshotApi } from "../api/services";
import { errorMessage } from "../api/client";
import { useAppStore } from "../stores/app";
import AbilityStar from "../components/charts/AbilityStar.vue";
import CapabilityMatrix from "../components/charts/CapabilityMatrix.vue";
import EventStream from "../components/charts/EventStream.vue";
import SignalFieldHero from "../components/SignalFieldHero.vue";
import LoadState from "../components/LoadState.vue";
import { DIMENSIONS, type CardDetail, type CardSummary, type MatrixRow, type SnapshotDetail, type SnapshotSummary } from "../types/api";

const store = useAppStore();
const loading = ref(false);
const error = ref("");
const cards = ref<CardSummary[]>([]);
const snapshots = ref<SnapshotSummary[]>([]);
const selectedEventTarget = ref("");
const radarSnapshots = ref<SnapshotDetail[]>([]);
const selectedRadarTargets = ref<string[]>([]);
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
const referenceOrder = computed(() => [...radarSeries.value]
  .sort((a, b) => b.snapshot.total_score - a.snapshot.total_score));
const coverageAverage = computed(() => snapshots.value.length
  ? Math.round(snapshots.value.reduce((sum, item) => sum + item.coverage_ratio, 0) / snapshots.value.length * 100)
  : 0);
const competitorColors: Record<string, string> = {
  Cursor: "#a34d3c",
  "GitHub Copilot": "#356ca3",
  Trae: "#9a753d",
  "通义灵码": "#3d93b8",
  CodeGeeX: "#7c71a1"
};
const radarSeries = computed(() => radarSnapshots.value.map((item) => ({
  competitor: item.snapshot.competitor,
  snapshot: item.snapshot,
  color: competitorColors[item.snapshot.competitor] || "#888"
})));
const displayedRadarSeries = computed(() => !selectedRadarTargets.value.length
  ? radarSeries.value
  : radarSeries.value.filter((item) => selectedRadarTargets.value.includes(item.competitor)));
const selectedRadarSnapshot = computed(() => selectedRadarTargets.value.length !== 1
  ? null
  : radarSnapshots.value.find((item) => item.snapshot.competitor === selectedRadarTargets.value[0]) || null);
const matrixProducts = computed(() => radarSeries.value.map((item) => item.competitor));
const matrixRows = computed<MatrixRow[]>(() => DIMENSIONS.map((dimension) => {
  const cells = radarSeries.value.map((item) => {
    const detail = item.snapshot.details.find((value) => value.dimension === dimension.key);
    const scored = detail?.status === "scored";
    return {
      product: item.competitor,
      dimension: dimension.key,
      status: scored ? "scored" as const : "insufficient_evidence" as const,
      score: scored ? detail.score : null,
      confidence: detail?.confidence || 0,
      evidence_count: detail?.evidence_count || 0,
      gap_to_baseline: null,
      delta_from_previous: null
    };
  });
  const scores = cells.flatMap((cell) => cell.score === null ? [] : [cell.score]);
  return {
    dimension: dimension.key,
    cells,
    valid_product_count: scores.length,
    mean_score: scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null,
    score_spread: scores.length ? Math.max(...scores) - Math.min(...scores) : null
  };
}));

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

function showAllRadarTargets() {
  selectedRadarTargets.value = [];
}

function toggleRadarTarget(competitor: string) {
  selectedRadarTargets.value = selectedRadarTargets.value.includes(competitor)
    ? selectedRadarTargets.value.filter((item) => item !== competitor)
    : [...selectedRadarTargets.value, competitor];
}

function requestHeroProgress() {
  if (!scrollFrame) scrollFrame = window.requestAnimationFrame(updateHeroProgress);
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await Promise.all([store.refreshStatus(), store.loadCompetitors()]);
    const [cardPage, snapshotPage] = await Promise.all([
      cardApi.list({ page: 1, page_size: 100, sort_by: "created_at", order: "desc" }),
      snapshotApi.list({ page: 1, page_size: 100 }),
    ]);
    cards.value = cardPage.items;
    snapshots.value = snapshotPage.items;
    // 从列表动态获取每个竞品的最新快照（列表已按 snapshot_date+created_at 降序排列）
    const latestByCompetitor = new Map<string, string>();
    for (const s of snapshotPage.items) {
      if (!latestByCompetitor.has(s.competitor)) {
        latestByCompetitor.set(s.competitor, s.snapshot_id);
      }
    }
    const latestIds = Array.from(latestByCompetitor.values());
    const radarDetails = await Promise.all(
      latestIds.map((id) => snapshotApi.detail(id))
    );
    radarSnapshots.value = radarDetails;
  } catch (reason) {
    error.value = errorMessage(reason);
  } finally {
    loading.value = false;
  }
}

async function openCard(card: CardSummary) {
  cardDrawer.value = true;
  cardDetail.value = null;
  try { cardDetail.value = await cardApi.detail(card.card_id); } catch { cardDetail.value = null; }
}

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
          <div><span>可追溯证据</span><strong>{{ store.ready?.indexed_chunks ?? 0 }}</strong><small>EVIDENCE</small></div>
          <div><span>平均覆盖</span><strong>{{ coverageAverage }}%</strong><small>COVERAGE</small></div>
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
              <div><span class="section-index">02</span><h2>能力雷达图</h2><p>轮廓位置表示能力得分，圆点大小表示对应维度的证据数量；可同时选择多个模型进行对比。</p></div>
              <div class="radar-switch" aria-label="能力雷达查看方式">
                <button
                  type="button"
                  :class="{ active: !selectedRadarTargets.length }"
                  @click="showAllRadarTargets"
                >全部对比</button>
                <button
                  v-for="item in radarSeries"
                  :key="item.competitor"
                  type="button"
                  :class="{ active: selectedRadarTargets.includes(item.competitor) }"
                  @click="toggleRadarTarget(item.competitor)"
                >{{ item.competitor }}</button>
              </div>
            </div>
            <AbilityStar :series="displayedRadarSeries" />
            <div class="metric-row">
              <template v-if="selectedRadarSnapshot">
                <span>总分 <strong>{{ selectedRadarSnapshot.snapshot.total_score.toFixed(1) }}</strong></span>
                <span>覆盖率 <strong>{{ Math.round(selectedRadarSnapshot.snapshot.coverage_ratio * 100) }}%</strong></span>
                <span>置信度 <strong>{{ Math.round(selectedRadarSnapshot.snapshot.overall_confidence * 100) }}%</strong></span>
              </template>
              <template v-else>
                <span>对比对象 <strong>{{ radarSeries.length }}</strong></span>
                <span>能力维度 <strong>7</strong></span>
              </template>
            </div>
          </section>

          <aside class="content-section ranking-section">
            <div class="section-heading"><div><span class="section-index">03</span><h2>参考排序</h2><p>基于当前完整能力快照</p></div></div>
            <el-empty v-if="!referenceOrder.length" description="暂无可比较结果" />
            <ol v-else class="ranking-list">
              <li v-for="(item, index) in referenceOrder" :key="item.competitor">
                <span class="ranking-index">{{ String(index + 1).padStart(2, '0') }}</span>
                <div><strong>{{ item.competitor }}</strong><small>覆盖充分</small></div>
                <b>{{ item.snapshot.total_score.toFixed(1) }}</b>
              </li>
            </ol>
          </aside>
        </div>

        <section class="content-section matrix-section">
          <div class="section-heading"><div><span class="section-index">04</span><h2>最新能力矩阵</h2><p>蓝色色阶越深表示能力得分越高，悬停可查看证据量与置信度。</p></div></div>
          <CapabilityMatrix v-if="matrixProducts.length" :rows="matrixRows" :products="matrixProducts" />
          <el-empty v-else description="暂无能力矩阵" />
        </section>

        <section class="content-section changes-section">
          <div class="section-heading"><div><span class="section-index">05</span><h2>最新关键变化</h2><p>按情报优先级排列，点击查看原始证据</p></div></div>
          <div class="signal-card-grid">
            <article v-for="(card, index) in importantCards" :key="card.card_id" class="signal-card" @click="openCard(card)">
              <div class="signal-card__top"><span>{{ String(index + 1).padStart(2, '0') }}</span><small>{{ card.competitor }} · {{ card.event_type }}</small></div>
              <h3>{{ card.event_title }}</h3><p>{{ card.summary }}</p>
              <div class="signal-card__meta"><span>{{ card.evidence_count }} 条证据</span><strong>{{ Math.round(card.confidence_score * 100) }}%</strong></div>
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
