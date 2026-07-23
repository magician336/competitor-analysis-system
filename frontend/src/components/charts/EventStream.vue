<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import type { CardSummary } from "../../types/api";

const props = defineProps<{ cards: CardSummary[]; allCards?: CardSummary[] }>();
const emit = defineEmits<{ select: [card: CardSummary] }>();
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;
let tooltip: HTMLDivElement | null = null;

const fixedModelColors: Record<string, string> = {
  Cursor: "#2f8cff",
  "GitHub Copilot": "#7767ef",
  Trae: "#f2a65a",
  "通义灵码": "#42c5a5",
  CodeGeeX: "#f36f82"
};
const fallbackColors = ["#2f8cff", "#7767ef", "#f2a65a", "#42c5a5", "#f36f82", "#7b8798"];

interface EventItem {
  card: CardSummary;
  date: Date;
  x: number;
  y: number;
  radius: number;
}

function publishedCards(cards: CardSummary[]) {
  return cards
    .map((card) => ({ card, date: card.publish_time ? new Date(card.publish_time) : null }))
    .filter((item): item is { card: CardSummary; date: Date } =>
      item.date !== null
      && !Number.isNaN(item.date.valueOf())
    );
}

function drawLegend(
  root: d3.Selection<HTMLDivElement, unknown, null, undefined>,
  models: string[],
  colorFor: (model: string) => string,
  priorityValues: number[],
  radiusFor: (priority: number) => number
) {
  const legend = root.append("div").attr("class", "event-stream__legend");
  const modelGroup = legend.append("div").attr("class", "event-stream__legend-group");
  modelGroup.append("strong").text("模型");
  const modelItems = modelGroup.selectAll("span.event-stream__legend-item")
    .data(models)
    .join("span")
    .attr("class", "event-stream__legend-item");
  modelItems.append("i").style("background-color", (model) => colorFor(model));
  modelItems.append("span").text((model) => model);

  const sorted = [...priorityValues].sort(d3.ascending);
  const median = Math.round(d3.median(sorted) ?? sorted[0]);
  const sizeValues = Array.from(new Set([sorted[0], median, sorted[sorted.length - 1]]));
  const sizeGroup = legend.append("div").attr("class", "event-stream__legend-group event-stream__size-legend");
  sizeGroup.append("strong").text("优先级");
  const sizeItems = sizeGroup.selectAll("span.event-stream__legend-size")
    .data(sizeValues)
    .join("span")
    .attr("class", "event-stream__legend-size");
  sizeItems.each(function (priority) {
    const radius = radiusFor(priority);
    const sample = d3.select(this);
    const icon = sample.append("svg").attr("width", 56).attr("height", 56).attr("aria-hidden", "true");
    icon.append("circle").attr("cx", 28).attr("cy", 28).attr("r", radius)
      .attr("fill", "#737b88").attr("fill-opacity", .82);
    sample.append("span").text(String(priority));
  });
}

function layoutBeeswarm(items: EventItem[], centerY: number, top: number, bottom: number) {
  const placed: EventItem[] = [];
  const fits = (item: EventItem, candidateY: number) => (
    candidateY - item.radius >= top
    && candidateY + item.radius <= bottom
    && placed.every((other) =>
      Math.hypot(item.x - other.x, candidateY - other.y) >= item.radius + other.radius + 4.5
    )
  );

  for (const item of [...items].sort((a, b) => a.x - b.x || b.radius - a.radius)) {
    const candidates = [centerY];
    for (const other of placed) {
      const requiredDistance = item.radius + other.radius + 4.5;
      const deltaX = Math.abs(item.x - other.x);
      if (deltaX >= requiredDistance) continue;
      const deltaY = Math.sqrt(requiredDistance ** 2 - deltaX ** 2);
      candidates.push(other.y - deltaY, other.y + deltaY);
    }
    candidates.sort((a, b) => Math.abs(a - centerY) - Math.abs(b - centerY));
    let selected = candidates.find((candidateY) => fits(item, candidateY));
    if (selected === undefined) {
      for (let offset = 2; offset <= bottom - top; offset += 2) {
        const upper = centerY - offset;
        const lower = centerY + offset;
        if (fits(item, upper)) { selected = upper; break; }
        if (fits(item, lower)) { selected = lower; break; }
      }
    }
    item.y = selected ?? centerY;
    placed.push(item);
  }
}

function stretchBeeswarm(items: EventItem[], top: number, bottom: number) {
  if (items.length < 2) return;
  const [minimumY, maximumY] = d3.extent(items, (item) => item.y) as [number, number];
  if (maximumY - minimumY < 1) return;
  const maximumRadius = d3.max(items, (item) => item.radius) ?? 0;
  const targetTop = top + maximumRadius;
  const targetBottom = bottom - maximumRadius;
  const available = targetBottom - targetTop;
  if (available <= maximumY - minimumY) return;
  const stretch = d3.scaleLinear().domain([minimumY, maximumY]).range([targetTop, targetBottom]);
  items.forEach((item) => { item.y = stretch(item.y); });
}

function draw() {
  if (!host.value) return;
  const root = d3.select(host.value);
  root.selectAll("*").remove();

  const visible = publishedCards(props.cards).sort((a, b) => a.date.valueOf() - b.date.valueOf());
  if (!visible.length) {
    root.append("div").attr("class", "chart-empty").text("暂无带发布时间的趋势事件");
    return;
  }

  const context = publishedCards(props.allCards?.length ? props.allCards : props.cards);
  const priorityValues = context.map((item) => item.card.priority_score);
  const scaleValues = priorityValues.map((priority) => Math.max(1, priority));
  const priorityMin = d3.min(scaleValues) ?? 1;
  const priorityMax = d3.max(scaleValues) ?? priorityMin;
  const logRadius = priorityMin === priorityMax
    ? null
    : d3.scaleLog().domain([priorityMin, priorityMax]).range([7.5, 24]).clamp(true);
  const radiusFor = (priority: number) => logRadius ? logRadius(Math.max(1, priority)) : 15;

  const models = Array.from(new Set(context.map((item) => item.card.competitor)))
    .sort((a, b) => a.localeCompare(b, "zh-CN"));
  const fallbackModels = models.filter((model) => !fixedModelColors[model]);
  const fallbackScale = d3.scaleOrdinal<string, string>().domain(fallbackModels).range(fallbackColors);
  const colorFor = (model: string) => fixedModelColors[model] || fallbackScale(model);
  drawLegend(root, models, colorFor, priorityValues, radiusFor);

  const width = Math.max(host.value.clientWidth, 620);
  const height = 350;
  const margin = { top: 24, right: 28, bottom: 52, left: 28 };
  const extent = d3.extent(visible, (item) => item.date) as [Date, Date];
  let start = extent[0];
  let end = extent[1];
  if (start.valueOf() === end.valueOf()) {
    start = new Date(start.valueOf() - 43_200_000);
    end = new Date(end.valueOf() + 43_200_000);
  } else {
    const padding = Math.max(3_600_000, (end.valueOf() - start.valueOf()) * .035);
    start = new Date(start.valueOf() - padding);
    end = new Date(end.valueOf() + padding);
  }
  const x = d3.scaleTime().domain([start, end]).range([margin.left, width - margin.right]);
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("role", "img")
    .attr("aria-label", "按真实发布时间展示的模型趋势事件流");

  const span = end.valueOf() - start.valueOf();
  const tickFormat = span <= 172_800_000 ? d3.timeFormat("%m-%d %H:%M") : d3.timeFormat("%Y-%m-%d");
  svg.append("g")
    .attr("transform", `translate(0,${height - margin.bottom + 8})`)
    .call(d3.axisBottom(x).ticks(Math.min(7, visible.length)).tickFormat(tickFormat as never).tickSizeOuter(0))
    .call((axis) => {
      axis.select(".domain").attr("stroke", "#cfd4dc");
      axis.selectAll(".tick text")
        .attr("font-size", 13)
        .attr("font-family", "Source Serif 4, STZhongsong, serif")
        .attr("fill", "#5f6672");
    });

  const centerY = (margin.top + height - margin.bottom) / 2;
  const layoutItems: EventItem[] = visible.map((item) => ({
    ...item,
    x: x(item.date),
    y: centerY,
    radius: radiusFor(item.card.priority_score)
  }));
  const swarmTop = margin.top + 4;
  const swarmBottom = height - margin.bottom - 4;
  layoutBeeswarm(layoutItems, centerY, swarmTop, swarmBottom);
  stretchBeeswarm(layoutItems, swarmTop, swarmBottom);

  svg.selectAll<SVGCircleElement, EventItem>("circle.event")
    .data(layoutItems, (item) => item.card.card_id)
    .join("circle")
    .attr("class", "event")
    .attr("data-card-id", (item) => item.card.card_id)
    .attr("data-model", (item) => item.card.competitor)
    .attr("data-event-type", (item) => item.card.event_type)
    .attr("data-priority", (item) => item.card.priority_score)
    .attr("data-publish-time", (item) => item.card.publish_time || "")
    .attr("cx", (item) => item.x)
    .attr("cy", (item) => item.y)
    .attr("r", 0)
    .attr("fill", (item) => colorFor(item.card.competitor))
    .attr("fill-opacity", .9)
    .attr("stroke", "#f4f1ea").attr("stroke-width", 2)
    .attr("tabindex", 0).attr("role", "button").style("cursor", "pointer")
    .on("click", (_, item) => emit("select", item.card))
    .on("mouseenter", (event, item) => {
      if (!tooltip) return;
      const publishDate = d3.timeFormat("%Y-%m-%d")(item.date);
      tooltip.textContent = `发布时间 ${publishDate} · 优先级 ${item.card.priority_score} · ${item.card.competitor} · ${item.card.event_title}`;
      tooltip.style.display = "block";
      tooltip.style.left = `${event.clientX + 10}px`;
      tooltip.style.top = `${event.clientY + 10}px`;
    })
    .on("mouseleave", () => { if (tooltip) tooltip.style.display = "none"; })
    .transition().duration(560).ease(d3.easeCubicOut)
    .attr("r", (item) => item.radius);
}

onMounted(() => {
  tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.style.display = "none";
  document.body.appendChild(tooltip);
  observer = new ResizeObserver(draw);
  if (host.value) observer.observe(host.value);
  draw();
});
watch(() => [props.cards, props.allCards], draw, { deep: true });
onBeforeUnmount(() => { observer?.disconnect(); tooltip?.remove(); });
</script>

<template><div ref="host" class="chart-box event-stream" data-testid="event-stream" /></template>
