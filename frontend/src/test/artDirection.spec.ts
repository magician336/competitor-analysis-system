import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App.vue";
import SignalFieldHero from "../components/SignalFieldHero.vue";
import { useAppStore } from "../stores/app";

describe("art-directed application shell", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses a top navigation without the legacy sidebar", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const store = useAppStore();
    store.refreshStatus = vi.fn();
    store.loadCompetitors = vi.fn();
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", component: { template: "<div>home</div>" } },
        { path: "/ask", component: { template: "<div>ask</div>" } },
        { path: "/evidence", component: { template: "<div>evidence</div>" } },
        { path: "/analysis", component: { template: "<div>analysis</div>" } },
        { path: "/admin", component: { template: "<div>admin</div>" } }
      ]
    });
    await router.push("/");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [pinia, router] } });

    expect(wrapper.find(".site-header").exists()).toBe(true);
    expect(wrapper.find(".sidebar").exists()).toBe(false);
    expect(wrapper.find(".navigation").text()).toContain("趋势观测");
    expect(wrapper.find(".navigation").text()).toContain("AI 随问");
    expect(wrapper.find(".navigation").text()).toContain("证据检索");
    expect(wrapper.find(".navigation").text()).toContain("深度报告");
    expect(wrapper.find(".navigation").text()).toContain("系统管理");
    expect(wrapper.find(".header-cta").exists()).toBe(false);
    const home = readFileSync(resolve(process.cwd(), "src/views/HomeView.vue"), "utf-8");
    expect(home).toContain("随便问问");
    expect(home).toContain("生成深度报告");
    expect(home.indexOf("随便问问")).toBeLessThan(home.indexOf("生成深度报告"));
    expect(home).toContain("追踪各模型重要更新与风险动态");
    expect(home).not.toContain("全部事件");
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf-8");
    expect(styles).toMatch(/\.section-index \{[^}]*font-size: 14px/);
    expect(styles).toMatch(/\.event-stream__legend \{[^}]*justify-content: center/);
  });

  it("renders the multi-orbit signal observatory and releases its animation frame", async () => {
    const context = {
      setTransform: vi.fn(), clearRect: vi.fn(), createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
      createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })), fillRect: vi.fn(), beginPath: vi.fn(),
      moveTo: vi.fn(), lineTo: vi.fn(), quadraticCurveTo: vi.fn(), stroke: vi.fn(), arc: vi.fn(), ellipse: vi.fn(), fill: vi.fn(), setLineDash: vi.fn()
    } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context);
    const cancel = vi.spyOn(window, "cancelAnimationFrame");
    const wrapper = mount(SignalFieldHero, { props: { progress: 0 } });
    expect(wrapper.find("canvas.signal-field").exists()).toBe(true);
    expect(wrapper.get("canvas").attributes("data-visual")).toBe("signal-observatory");
    expect(wrapper.get("canvas").attributes("data-orbit-count")).toBe("10");
    expect(wrapper.get("canvas").attributes("data-mobile-orbit-count")).toBe("7");
    expect(wrapper.get("canvas").attributes("data-node-count")).toBe("16");
    expect(wrapper.get("canvas").attributes("data-mobile-node-count")).toBe("10");
    expect(wrapper.get("canvas").attributes("data-interaction")).toBe("lens-refraction scan-ripple");
    expect(context.ellipse).toHaveBeenCalled();
    expect(context.lineTo).toHaveBeenCalled();
    for (const progress of [0.35, 0.6, 0.82, 1]) {
      await wrapper.setProps({ progress });
      expect(context.lineTo).toHaveBeenCalled();
    }
    wrapper.unmount();
    expect(cancel).toHaveBeenCalled();
  });

  it("keeps the sticky hero out of accidental overflow scroll containers", () => {
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf-8");
    expect(styles).toMatch(/html, body, #app \{[^}]*overflow-x: clip/);
    expect(styles).toMatch(/\.main-content \{[^}]*overflow-x: clip/);
    expect(styles).not.toMatch(/\.main-content \{[^}]*overflow-x: hidden/);
  });

  it("uses a warm-light hero and removes the legacy fixed center point", () => {
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf-8");
    const component = readFileSync(resolve(process.cwd(), "src/components/SignalFieldHero.vue"), "utf-8");
    expect(styles).toMatch(/\.hero-narrative \{[^}]*background: var\(--paper\)/);
    expect(styles).toMatch(/\.hero-narrative \{[^}]*height: 310svh/);
    expect(styles).toMatch(/\.hero-focus-copy \{[^}]*left: 50%[^}]*text-align: center/);
    expect(styles).toMatch(/\.hero-focus-copy__title strong \{[^}]*font-size: clamp\(40px,5\.6vw,78px\)/);
    expect(component).not.toContain("context.arc(centerX, centerY, 3.5");
    expect(component).toContain("const orbits: OrbitSpec[]");
    expect(component).toContain("const pointerProximity");
    expect(component).toContain("scanStartedAt = performance.now()");
    expect(component).toContain("for (let segment = 0; segment < 4; segment += 1)");
    expect(component).toContain("const activeOrbits = compact ? orbits.slice(0, 7) : orbits");
    expect(component).toContain("const nodeCount = compact ? 10 : 16");
    expect(component).toContain("lensRadius * 1.55");
    expect(component).toContain("/ 1250");
    expect(styles).toMatch(/\.hero-handoff__heading h2 \{[^}]*font-size: clamp\(32px,3\.5vw,52px\)/);
    expect(styles).toMatch(/\.analysis-tabs \.el-tabs__item \{[^}]*font-size: 18px/);
    expect(styles).toMatch(/\.wizard-progress button strong \{[^}]*font-size: 16px/);
  });
});
