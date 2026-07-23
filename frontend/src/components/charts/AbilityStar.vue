<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import { DIMENSIONS, type CapabilitySnapshot } from "../../types/api";

const props = defineProps<{ snapshot: CapabilitySnapshot | null }>();
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;
let tooltip: HTMLDivElement | null = null;

function draw() {
  if (!host.value) return;
  const root = d3.select(host.value);
  root.selectAll("*").remove();
  if (!props.snapshot) {
    root.append("div").attr("class", "chart-empty").text("暂无能力快照");
    return;
  }
  const width = Math.max(host.value.clientWidth, 420);
  const height = 390;
  const radius = Math.min(width, height) * .33;
  const center = [width / 2, height / 2] as const;
  const details = new Map(props.snapshot.details.map((item) => [item.dimension, item]));
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("role", "img");
  const defs = svg.append("defs");
  const gradient = defs.append("linearGradient").attr("id", "ability-gradient").attr("x1", "0%").attr("y1", "0%").attr("x2", "100%").attr("y2", "100%");
  gradient.append("stop").attr("offset", "0%").attr("stop-color", "#2f8cff");
  gradient.append("stop").attr("offset", "52%").attr("stop-color", "#7b67ee");
  gradient.append("stop").attr("offset", "100%").attr("stop-color", "#55d7c0");
  const group = svg.append("g").attr("transform", `translate(${center[0]},${center[1]})`);
  const angle = (index: number) => index * Math.PI * 2 / DIMENSIONS.length - Math.PI / 2;
  const point = (index: number, value: number) => [
    Math.cos(angle(index)) * radius * value / 100,
    Math.sin(angle(index)) * radius * value / 100
  ] as const;

  [25, 50, 75, 100].forEach((level) => {
    group.append("polygon")
      .attr("points", DIMENSIONS.map((_, index) => point(index, level).join(",")).join(" "))
      .attr("fill", "none").attr("stroke", "rgba(28, 35, 47, .12)").attr("stroke-dasharray", level === 100 ? null : "2 5");
  });
  DIMENSIONS.forEach((dimension, index) => {
    const outer = point(index, 100);
    const label = point(index, 118);
    group.append("line").attr("x2", outer[0]).attr("y2", outer[1]).attr("stroke", "rgba(28, 35, 47, .15)");
    group.append("text").attr("x", label[0]).attr("y", label[1])
      .attr("text-anchor", label[0] > 8 ? "start" : label[0] < -8 ? "end" : "middle")
      .attr("dominant-baseline", "middle").attr("font-size", 14).attr("font-family", "Source Serif 4, STZhongsong, serif").attr("fill", "#3e4653")
      .text(`${dimension.code} ${dimension.name}`);
  });

  const values = DIMENSIONS.map((dimension) => {
    const detail = details.get(dimension.key);
    return detail?.status === "scored" ? detail.score : null;
  });
  const line = d3.lineRadial<number | null>()
    .angle((_, index) => angle(index) + Math.PI / 2)
    .radius((value) => radius * Number(value) / 100)
    .defined((value) => value !== null)
    .curve(values.every((value) => value !== null) ? d3.curveLinearClosed : d3.curveLinear);
  group.append("path").datum(values).attr("d", line)
    .attr("fill", values.every((value) => value !== null) ? "url(#ability-gradient)" : "none")
    .attr("fill-opacity", .14).attr("stroke", "url(#ability-gradient)").attr("stroke-width", 2.2)
    .attr("stroke-linejoin", "round").attr("stroke-dasharray", 900).attr("stroke-dashoffset", 900)
    .transition().duration(680).ease(d3.easeCubicOut).attr("stroke-dashoffset", 0);

  DIMENSIONS.forEach((dimension, index) => {
    const detail = details.get(dimension.key);
    if (!detail || detail.status !== "scored") {
      const coords = point(index, 100);
      group.append("circle").attr("cx", coords[0]).attr("cy", coords[1]).attr("r", 6)
        .attr("fill", "none").attr("stroke", "#9ca3af").attr("stroke-dasharray", "2 2")
        .attr("data-status", "insufficient_evidence");
      return;
    }
    const coords = point(index, detail.score);
    group.append("circle").attr("cx", coords[0]).attr("cy", coords[1])
      .attr("r", 4 + Math.min(detail.evidence_count, 8) * .65)
      .attr("fill", "#4a83ef").attr("fill-opacity", .25 + detail.confidence * .75)
      .attr("stroke", "#f4f1ea").attr("stroke-width", 1.5).attr("data-status", "scored")
      .on("mouseenter", (event) => {
        if (!tooltip) return;
        tooltip.textContent = `${dimension.code} ${dimension.name}：${detail.score} · 置信度 ${Math.round(detail.confidence * 100)}% · ${detail.evidence_count} 条证据`;
        tooltip.style.display = "block";
        tooltip.style.left = `${event.clientX + 10}px`;
        tooltip.style.top = `${event.clientY + 10}px`;
      })
      .on("mouseleave", () => { if (tooltip) tooltip.style.display = "none"; });
  });
  const legend = svg.append("g").attr("transform", `translate(${Math.max(12, width - 286)},${height - 22})`);
  legend.append("line").attr("x2", 74).attr("stroke", "url(#ability-gradient)").attr("stroke-width", 5);
  legend.append("text").attr("x", 84).attr("y", 5).attr("font-size", 13).attr("fill", "#6f7280").text("分数 · 透明度代表置信度");
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
watch(() => props.snapshot, draw, { deep: true });
onBeforeUnmount(() => { observer?.disconnect(); tooltip?.remove(); });
</script>

<template><div ref="host" class="chart-box ability-star" data-testid="ability-star" /></template>
