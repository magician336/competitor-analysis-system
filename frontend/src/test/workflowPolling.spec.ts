import { describe, expect, it, vi } from "vitest";
import { createWorkflowPoller } from "../composables/workflowPolling";

describe("workflow polling", () => {
  it("stops after a terminal status", async () => {
    vi.useFakeTimers();
    const fetchStatus = vi.fn().mockResolvedValueOnce("running").mockResolvedValueOnce("success");
    const poller = createWorkflowPoller(fetchStatus, 1000);
    poller.start("workflow_1");
    await vi.runOnlyPendingTimersAsync();
    await vi.runOnlyPendingTimersAsync();
    expect(fetchStatus).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchStatus).toHaveBeenCalledTimes(2);
    vi.useRealTimers();
  });

  it("stops when explicitly cancelled", async () => {
    vi.useFakeTimers();
    const fetchStatus = vi.fn().mockResolvedValue("running");
    const poller = createWorkflowPoller(fetchStatus, 1000);
    poller.start("workflow_2");
    await Promise.resolve();
    poller.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetchStatus).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
