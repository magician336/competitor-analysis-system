import type { WorkflowStatus } from "../types/api";

export const TERMINAL_WORKFLOW_STATUSES = new Set<WorkflowStatus>([
  "cancelled", "success", "partial_failure", "failed", "timed_out"
]);

export function createWorkflowPoller(
  fetchStatus: (workflowId: string) => Promise<WorkflowStatus>,
  intervalMs = 1500
) {
  let timer: number | null = null;
  let generation = 0;

  function stop() {
    generation += 1;
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  }

  async function cycle(workflowId: string, currentGeneration: number) {
    let status: WorkflowStatus;
    try {
      status = await fetchStatus(workflowId);
    } catch {
      timer = null;
      return;
    }
    if (generation !== currentGeneration || TERMINAL_WORKFLOW_STATUSES.has(status)) {
      timer = null;
      return;
    }
    timer = window.setTimeout(() => void cycle(workflowId, currentGeneration), intervalMs);
  }

  function start(workflowId: string) {
    stop();
    const currentGeneration = generation;
    void cycle(workflowId, currentGeneration);
  }

  return { start, stop, isActive: () => timer !== null };
}
