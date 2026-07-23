<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import type { SnapshotSummary } from "../../types/api";

const props = defineProps<{ snapshots: SnapshotSummary[] }>();
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;

function draw() {
  if (!host.value || !props.snapshots.length) return;
  const width = Math.max(host.value.clientWidth, 520);
  const height = 360;
  const margin = { top: 24, right: 26, bottom: 45, left: 50 };
  const root = d3.select(host.value);
  root.selectAll("*").remove();
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`);
  const parsed = props.snapshots.map((item) => ({ ...item, date: new Date(item.snapshot_date) }));
  const dates = parsed.map((item) => item.date);
  const extent = d3.extent(dates) as [Date, Date];
  const sameDay = extent[0].getTime() === extent[1].getTime();
  const x = d3.scaleTime()
    .domain(sameDay ? [new Date(extent[0].getTime() - 86400000), new Date(extent[1].getTime() + 86400000)] : extent)
    .range([margin.left, width - margin.right]);
  const y = d3.scaleLinear().domain([0, 100]).range([height - margin.bottom, margin.top]);
  svg.append("g").attr("transform", `translate(0,${height - margin.bottom})`).call(d3.axisBottom(x).ticks(5).tickFormat(d3.timeFormat("%m-%d") as never));
  svg.append("g").attr("transform", `translate(${margin.left},0)`).call(d3.axisLeft(y).ticks(5));
  const groups = d3.group(parsed, (item) => item.competitor);
  const color = d3.scaleOrdinal<string, string>(d3.schemeTableau10);
  groups.forEach((items, competitor) => {
    items.sort((a, b) => a.date.getTime() - b.date.getTime());
    svg.append("path").datum(items).attr("fill", "none").attr("stroke", color(competitor)).attr("stroke-width", 2)
      .attr("d", d3.line<(typeof items)[number]>().x((item) => x(item.date)).y((item) => y(item.total_score)));
    svg.append("g").attr("data-product", competitor).selectAll("circle").data(items).enter().append("circle")
      .attr("cx", (item) => x(item.date)).attr("cy", (item) => y(item.total_score)).attr("r", 4).attr("fill", color(competitor));
  });
  const legend = svg.append("g").attr("transform", `translate(${margin.left + 8},${margin.top})`);
  Array.from(groups.keys()).forEach((name, index) => {
    const item = legend.append("g").attr("transform", `translate(${index * 120},0)`);
    item.append("circle").attr("r", 4).attr("fill", color(name));
    item.append("text").attr("x", 8).attr("y", 4).attr("font-size", 12).text(name);
  });
}

onMounted(() => { observer = new ResizeObserver(draw); if (host.value) observer.observe(host.value); draw(); });
watch(() => props.snapshots, draw, { deep: true });
onBeforeUnmount(() => observer?.disconnect());
</script>

<template><div ref="host" class="chart-box" data-testid="trend-chart" /></template>
