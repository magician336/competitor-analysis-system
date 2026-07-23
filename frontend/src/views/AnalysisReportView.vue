<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { briefingApi, cardApi, snapshotApi, workflowApi } from "../api/services";
import { errorMessage } from "../api/client";
import { renderMarkdown } from "../utils/markdown";
import LoadState from "../components/LoadState.vue";
import ReportArtifacts from "../components/ReportArtifacts.vue";
import { analysisTimeLabel, isAnalysisTimePreset } from "../utils/analysisTime";
import type { BriefingDetail, CardDetail, SnapshotDetail, WorkflowDetail } from "../types/api";

const route = useRoute();
const router = useRouter();
const workflowId = String(route.params.workflowId);
const loading = ref(true);
const error = ref("");
const detail = ref<WorkflowDetail | null>(null);
const briefing = ref<BriefingDetail | null>(null);
const snapshot = ref<SnapshotDetail | null>(null);
const cards = ref<CardDetail[]>([]);
const artifactDrawer = ref(false);
const html = computed(() => renderMarkdown(briefing.value?.markdown || ""));
const timePreset = computed(() => isAnalysisTimePreset(route.query.range) ? route.query.range : null);
const timeRangeLabel = computed(() => analysisTimeLabel(timePreset.value));

function backToHistory() {
  void router.push({ path: "/analysis", query: { tab: "history" } });
}

function download() {
  if (briefing.value) void briefingApi.download(briefing.value.briefing_id);
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    detail.value = await workflowApi.detail(workflowId);
    if (!detail.value.briefing_id) throw new Error("该分析任务没有可阅读的 Markdown 简报");
    const [briefingResult, snapshotResult, cardResults] = await Promise.all([
      briefingApi.detail(detail.value.briefing_id),
      detail.value.snapshot_id ? snapshotApi.detail(detail.value.snapshot_id).catch(() => null) : null,
      Promise.all(detail.value.card_ids.map((id) => cardApi.detail(id).catch(() => null))),
    ]);
    briefing.value = briefingResult;
    snapshot.value = snapshotResult;
    cards.value = cardResults.filter((item): item is CardDetail => item !== null);
  } catch (reason) { error.value = errorMessage(reason); }
  finally { loading.value = false; }
}

onMounted(load);
</script>

<template>
  <div class="report-workspace route-grid" :data-analysis-mode="detail?.analysis_mode || 'rules'">
    <LoadState :loading="loading" :error="error" @retry="load">
      <template v-if="detail && briefing">
        <div class="report-split">
          <aside class="report-sidebar">
            <ReportArtifacts :detail="detail" :briefing="briefing" :snapshot="snapshot" :cards="cards" :time-range-label="timeRangeLabel" @back="backToHistory" @download="download" />
          </aside>
          <main id="report-content" class="report-reader">
            <button type="button" class="mobile-artifacts-trigger" @click="artifactDrawer = true">本次分析产物 <span>＋</span></button>
            <article class="report-reader__paper">
              <div v-html="html" class="markdown-body" />
              <section id="evidence-index" class="reader-evidence">
                <h2>关联情报来源</h2>
                <p class="muted">简报中的判断来自本次 {{ cards.length }} 张结构化情报卡片，以下来源可用于复核。</p>
                <div v-for="card in cards" :key="card.card.card_id" class="reader-evidence__group">
                  <h3>{{ card.card.event_title }}</h3>
                  <a v-for="item in card.evidence_links" :key="item.chunk_id" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }} · {{ item.evidence_level }}</a>
                </div>
              </section>
            </article>
          </main>
        </div>
        <el-drawer v-model="artifactDrawer" direction="ltr" size="min(92vw, 390px)" :with-header="false" class="artifact-mobile-drawer">
          <ReportArtifacts :detail="detail" :briefing="briefing" :snapshot="snapshot" :cards="cards" :time-range-label="timeRangeLabel" @back="backToHistory" @download="download" />
        </el-drawer>
      </template>
    </LoadState>
  </div>
</template>
