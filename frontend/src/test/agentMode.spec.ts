import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useAppStore } from "../stores/app";

describe("Agent mode selection", () => {
  beforeEach(() => {
    sessionStorage.clear();
    setActivePinia(createPinia());
  });

  it("persists the selected mode for new Workflow submissions", () => {
    const store = useAppStore();
    expect(store.agentMode).toBe("rules");

    store.setAgentMode("hybrid");

    expect(store.agentMode).toBe("hybrid");
    expect(sessionStorage.getItem("coderadar-agent-mode")).toBe("hybrid");
  });
});
