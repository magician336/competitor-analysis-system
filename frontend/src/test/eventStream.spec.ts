import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import EventStream from "../components/charts/EventStream.vue";
import type { CardSummary } from "../types/api";

function card(overrides: Partial<CardSummary> & Pick<CardSummary, "card_id" | "competitor" | "event_type" | "publish_time">): CardSummary {
  return {
    agent_kind: "product",
    event_title: "model event",
    summary: "summary",
    alert_level: "orange",
    confidence_score: .8,
    priority_score: 75,
    review_required: false,
    evidence_count: 2,
    created_at: "2026-07-22T12:00:00Z",
    ...overrides
  } as CardSummary;
}

const cards: CardSummary[] = [
  card({
    card_id: "price",
    competitor: "Cursor",
    event_type: "pricing_change",
    event_title: "Cursor price event",
    priority_score: 20,
    publish_time: "2026-05-01T00:00:00Z",
    created_at: "2026-07-22T12:00:00Z"
  }),
  card({
    card_id: "product",
    competitor: "GitHub Copilot",
    event_type: "product_release",
    event_title: "Copilot product event",
    priority_score: 75,
    publish_time: "2026-06-01T00:00:00Z",
    created_at: "2026-04-01T00:00:00Z"
  }),
  card({
    card_id: "risk",
    competitor: "Trae",
    event_type: "risk_experience",
    event_title: "Trae risk event",
    priority_score: 87,
    publish_time: "2026-07-01T00:00:00Z"
  }),
  card({
    card_id: "missing-time",
    competitor: "CodeGeeX",
    event_type: "product_release",
    priority_score: 80,
    publish_time: null
  })
];

afterEach(() => {
  document.querySelectorAll(".chart-tooltip").forEach((item) => item.remove());
});

describe("trend event stream encodings", () => {
  it("uses publish time, a non-semantic y position, circles, and excludes missing publication time", () => {
    const wrapper = mount(EventStream, { props: { cards, allCards: cards } });
    const events = wrapper.findAll("circle.event");
    expect(events).toHaveLength(3);
    expect(wrapper.find('[data-card-id="missing-time"]').exists()).toBe(false);
    expect(events.every((item) => item.element.tagName.toLowerCase() === "circle")).toBe(true);
    expect(wrapper.text()).not.toContain("价格变化");
    expect(wrapper.text()).not.toContain("产品更新");
    expect(wrapper.text()).not.toContain("风险信号");
    expect(wrapper.find(".observable-grid").exists()).toBe(false);

    const priceX = Number(wrapper.get('[data-card-id="price"]').attributes("cx"));
    const productX = Number(wrapper.get('[data-card-id="product"]').attributes("cx"));
    expect(priceX).toBeLessThan(productX);
  });

  it("keeps equal publication times on the same x coordinate and vertically avoids overlap", () => {
    const sameTimeCards = [
      card({
        card_id: "same-time-1",
        competitor: "Cursor",
        event_type: "product_release",
        priority_score: 87,
        publish_time: "2026-06-01T00:00:00Z"
      }),
      card({
        card_id: "same-time-2",
        competitor: "Trae",
        event_type: "pricing_change",
        priority_score: 80,
        publish_time: "2026-06-01T00:00:00Z"
      }),
      card({
        card_id: "same-time-3",
        competitor: "CodeGeeX",
        event_type: "risk_experience",
        priority_score: 70,
        publish_time: "2026-06-01T00:00:00Z"
      })
    ];
    const wrapper = mount(EventStream, { props: { cards: sameTimeCards, allCards: sameTimeCards } });
    const events = wrapper.findAll("circle.event");
    expect(new Set(events.map((item) => item.attributes("cx"))).size).toBe(1);
    expect(new Set(events.map((item) => item.attributes("cy"))).size).toBeGreaterThan(1);
    const positions = events.map((item) => Number(item.attributes("cy")));
    expect(Math.min(...positions)).toBeLessThanOrEqual(60);
    expect(Math.max(...positions)).toBeGreaterThanOrEqual(260);

    const eventData = events.map((item) =>
      (item.element as Element & { __data__: { y: number; radius: number } }).__data__
    );
    for (let left = 0; left < eventData.length; left += 1) {
      for (let right = left + 1; right < eventData.length; right += 1) {
        expect(Math.abs(eventData[left].y - eventData[right].y))
          .toBeGreaterThanOrEqual(eventData[left].radius + eventData[right].radius + 4.5);
      }
    }
  });

  it("keeps model colors stable and maps priority through a monotonic logarithmic radius", async () => {
    const wrapper = mount(EventStream, { props: { cards, allCards: cards } });
    const price = wrapper.get('[data-card-id="price"]');
    const product = wrapper.get('[data-card-id="product"]');
    const risk = wrapper.get('[data-card-id="risk"]');
    const radius = (element: Element) => (element as Element & { __data__: { radius: number } }).__data__.radius;
    expect(radius(price.element)).toBeCloseTo(7.5);
    expect(radius(risk.element)).toBeCloseTo(24);
    expect(radius(price.element)).toBeLessThan(radius(product.element));
    expect(radius(product.element)).toBeLessThan(radius(risk.element));

    const cursorColor = price.attributes("fill");
    await wrapper.setProps({ cards: [cards[0]], allCards: cards });
    await nextTick();
    expect(wrapper.get('[data-card-id="price"]').attributes("fill")).toBe(cursorColor);
    expect(wrapper.text()).toContain("模型");
    expect(wrapper.text()).toContain("优先级");
    expect(wrapper.text()).toContain("GitHub Copilot");
  });

  it("puts publication time and priority before the model and title in the tooltip", async () => {
    const wrapper = mount(EventStream, { props: { cards, allCards: cards } });
    await wrapper.get('[data-card-id="product"]').trigger("mouseenter", { clientX: 10, clientY: 20 });
    const tooltip = document.body.querySelector(".chart-tooltip");
    expect(tooltip?.textContent).toBe("发布时间 2026-06-01 · 优先级 75 · GitHub Copilot · Copilot product event");
    expect(tooltip?.textContent).not.toContain("置信度");
    expect(tooltip?.textContent).not.toContain("证据");
  });

  it("shows the publication-time empty state without falling back to card creation time", () => {
    const missingOnly = cards.filter((item) => item.publish_time === null);
    const wrapper = mount(EventStream, { props: { cards: missingOnly, allCards: cards } });
    expect(wrapper.text()).toContain("暂无带发布时间的趋势事件");
    expect(wrapper.find("circle.event").exists()).toBe(false);
  });
});
