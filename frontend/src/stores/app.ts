import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { competitorApi, systemApi } from "../api/services";
import type { AgentMode, Competitor, ReadyStatus } from "../types/api";

const MODE_STORAGE_KEY = "coderadar-agent-mode";
const AGENT_MODES: AgentMode[] = ["rules", "hybrid", "llm"];

function initialAgentMode(): AgentMode {
  const saved = typeof sessionStorage === "undefined" ? null : sessionStorage.getItem(MODE_STORAGE_KEY);
  return AGENT_MODES.includes(saved as AgentMode) ? (saved as AgentMode) : "rules";
}

export const useAppStore = defineStore("app", () => {
  const competitors = ref<Competitor[]>([]);
  const ready = ref<ReadyStatus | null>(null);
  const apiOnline = ref(false);
  const statusLoading = ref(false);
  const agentMode = ref<AgentMode>(initialAgentMode());

  const enabledCompetitors = computed(() => competitors.value.filter((item) => item.enabled));

  async function refreshStatus() {
    statusLoading.value = true;
    try {
      await systemApi.health();
      apiOnline.value = true;
      ready.value = await systemApi.ready();
    } catch {
      apiOnline.value = false;
      ready.value = null;
    } finally {
      statusLoading.value = false;
    }
  }

  async function loadCompetitors() {
    competitors.value = await competitorApi.list();
  }

  function setAgentMode(mode: AgentMode) {
    agentMode.value = mode;
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(MODE_STORAGE_KEY, mode);
  }

  return {
    competitors,
    enabledCompetitors,
    ready,
    apiOnline,
    statusLoading,
    agentMode,
    refreshStatus,
    loadCompetitors,
    setAgentMode
  };
});
