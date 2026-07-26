<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import { DIMENSIONS, type CapabilitySnapshot } from "../../types/api";

interface RadarSeries {
  competitor: string;
  snapshot: CapabilitySnapshot;
  color: string;
}

const props = withDefaults(defineProps<{
  snapshot?: CapabilitySnapshot | null;
  series?: RadarSeries[];
}>(), {
  snapshot: null,
  series: () => []
});
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;
let tooltip: HTMLDivElement | null = null;

function visibleSeries() {
  if (props.series.length) return props.series;
  return props.snapshot ? [{ competitor: "当前模型", snapshot: props.snapshot, color: "#356ca3" }] : [];
}

function draw() {
  if (!host.value) return;
  const root = d3.select(host.value);
  root.selectAll("*").remove();
  const series = visibleSeries();
  if (!series.length) {
    root.append("div").attr("class", "chart-empty").text("暂无固定能力快照");
    return;
  }

  const comparison = series.length > 1;
  const legend = root.append("div").attr("class", "ability-star__legend");
  series.forEach((item) => {
    const entry = legend.append("span");
    entry.append("i").style("background-color", item.color);
    entry.append("b").text(item.competitor);
  });

  const width = Math.max(host.value.clientWidth, 420);
  const height = 390;
  const radius = Math.min(width, height) * .33;
  const center = [width / 2, height / 2] as const;
  const svg = root.append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", comparison ? "五个竞品的能力雷达对比" : `${series[0].competitor} 的能力雷达图`);
  const group = svg.append("g").attr("transform", `translate(${center[0]},${center[1]})`);
  const angle = (index: number) => index * Math.PI * 2 / DIMENSIONS.length - Math.PI / 2;
  const point = (index: number, value: number) => [
    Math.cos(angle(index)) * radius * value / 100,
    Math.sin(angle(index)) * radius * value / 100
  ] as const;

  [25, 50, 75, 100].forEach((level) => {
    group.append("polygon")
      .attr("points", DIMENSIONS.map((_, index) => point(index, level).join(",")).join(" "))
      .attr("fill", "none")
      .attr("stroke", "rgba(28, 35, 47, .12)")
      .attr("stroke-dasharray", level === 100 ? null : "2 5");
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

  series.forEach((item) => {
    const details = new Map(item.snapshot.details.map((detail) => [detail.dimension, detail]));
    const values = DIMENSIONS.map((dimension) => {
      const detail = details.get(dimension.key);
      return detail?.status === "scored" ? detail.score : null;
    });
    const complete = values.every((value) => value !== null);
    const line = d3.lineRadial<number | null>()
      .angle((_, index) => angle(index) + Math.PI / 2)
      .radius((value) => radius * Number(value) / 100)
      .defined((value) => value !== null)
      .curve(complete ? d3.curveLinearClosed : d3.curveLinear);
    group.append("path").datum(values).attr("d", line)
      .attr("fill", complete ? item.color : "none")
      .attr("fill-opacity", comparison ? .055 : .13)
      .attr("stroke", item.color).attr("stroke-width", comparison ? 2.1 : 2.4)
      .attr("stroke-linejoin", "round").attr("stroke-dasharray", 900).attr("stroke-dashoffset", 900)
      .transition().duration(680).ease(d3.easeCubicOut).attr("stroke-dashoffset", 0);

    DIMENSIONS.forEach((dimension, index) => {
      const detail = details.get(dimension.key);
      if (!detail || detail.status !== "scored") {
        if (!comparison) {
          const coords = point(index, 100);
          group.append("circle").attr("cx", coords[0]).attr("cy", coords[1]).attr("r", 6)
            .attr("fill", "none").attr("stroke", "#9ca3af").attr("stroke-dasharray", "2 2")
            .attr("data-status", "insufficient_evidence");
        }
        return;
      }
      const coords = point(index, detail.score);
      group.append("circle").attr("cx", coords[0]).attr("cy", coords[1])
        .attr("r", comparison ? 2.5 + Math.min(detail.evidence_count, 8) * .35 : 4 + Math.min(detail.evidence_count, 8) * .65)
        .attr("fill", item.color).attr("fill-opacity", 1)
        .attr("stroke", "none").attr("data-status", "scored")
        .on("mouseenter", (event) => {
          if (!tooltip) return;
          tooltip.textContent = `${item.competitor} · ${dimension.code} ${dimension.name}：${detail.score} · 置信度 ${Math.round(detail.confidence * 100)}% · ${detail.evidence_count} 条证据`;
          tooltip.style.display = "block";
          tooltip.style.left = `${event.clientX + 10}px`;
          tooltip.style.top = `${event.clientY + 10}px`;
        })
        .on("mouseleave", () => { if (tooltip) tooltip.style.display = "none"; });
    });
  });
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
watch(() => [props.snapshot, props.series], draw, { deep: true });
onBeforeUnmount(() => { observer?.disconnect(); tooltip?.remove(); });
</script>

<template><div ref="host" class="chart-box ability-star" data-testid="ability-star" /></template>
