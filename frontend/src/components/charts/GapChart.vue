<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import { DIMENSIONS, type GapTrend } from "../../types/api";

const props = defineProps<{ trends: GapTrend[] }>();
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;

function draw() {
  if (!host.value || !props.trends.length) return;
  const width = Math.max(host.value.clientWidth, 520);
  const rowHeight = 34;
  const height = props.trends.length * rowHeight + 48;
  const margin = { top: 12, right: 25, bottom: 32, left: 150 };
  const root = d3.select(host.value);
  root.selectAll("*").remove();
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`);
  const max = d3.max(props.trends, (item) => Math.abs(item.absolute_gap_change)) || 1;
  const x = d3.scaleLinear().domain([-max, max]).range([margin.left, width - margin.right]);
  const y = d3.scaleBand().domain(props.trends.map((_, i) => String(i))).range([margin.top, height - margin.bottom]).padding(.25);
  svg.append("g").attr("transform", `translate(0,${height - margin.bottom})`).call(d3.axisBottom(x).ticks(5));
  svg.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", margin.top).attr("y2", height - margin.bottom).attr("stroke", "#909399");
  props.trends.forEach((item, index) => {
    const dimension = DIMENSIONS.find((entry) => entry.key === item.dimension);
    svg.append("text").attr("x", margin.left - 8).attr("y", Number(y(String(index))) + y.bandwidth() / 2 + 4)
      .attr("text-anchor", "end").attr("font-size", 12).text(`${item.product} · ${dimension?.code || item.dimension}`);
    svg.append("rect").attr("x", Math.min(x(0), x(item.absolute_gap_change))).attr("y", y(String(index)) || 0)
      .attr("width", Math.abs(x(item.absolute_gap_change) - x(0))).attr("height", y.bandwidth())
      .attr("fill", item.absolute_gap_change >= 0 ? "#67c23a" : "#f56c6c");
  });
}

onMounted(() => { observer = new ResizeObserver(draw); if (host.value) observer.observe(host.value); draw(); });
watch(() => props.trends, draw, { deep: true });
onBeforeUnmount(() => observer?.disconnect());
</script>

<template><div ref="host" class="chart-box" data-testid="gap-chart" /></template>
