<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import { DIMENSIONS, type MatrixRow } from "../../types/api";

const props = defineProps<{ rows: MatrixRow[]; products: string[] }>();
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;
let tooltip: HTMLDivElement | null = null;

function draw() {
  if (!host.value) return;
  const width = Math.max(host.value.clientWidth, 420);
  const height = 400;
  const radius = Math.min(width, height) * 0.34;
  const center = [width / 2, height / 2] as const;
  const root = d3.select(host.value);
  root.selectAll("*").remove();
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("role", "img");
  const group = svg.append("g").attr("transform", `translate(${center[0]},${center[1]})`);
  const angle = (index: number) => index * Math.PI * 2 / DIMENSIONS.length - Math.PI / 2;
  const point = (index: number, value: number) => [
    Math.cos(angle(index)) * radius * value / 100,
    Math.sin(angle(index)) * radius * value / 100
  ] as const;

  [20, 40, 60, 80, 100].forEach((level) => {
    const points = DIMENSIONS.map((_, index) => point(index, level));
    group.append("polygon")
      .attr("points", points.map((item) => item.join(",")).join(" "))
      .attr("fill", "none").attr("stroke", "#dcdfe6");
  });
  DIMENSIONS.forEach((dimension, index) => {
    const outer = point(index, 100);
    const label = point(index, 116);
    group.append("line").attr("x2", outer[0]).attr("y2", outer[1]).attr("stroke", "#dcdfe6");
    group.append("text").attr("x", label[0]).attr("y", label[1])
      .attr("text-anchor", label[0] > 8 ? "start" : label[0] < -8 ? "end" : "middle")
      .attr("dominant-baseline", "middle").attr("font-size", 12)
      .text(`${dimension.code} ${dimension.name}`);
  });

  const colors = d3.scaleOrdinal<string, string>(d3.schemeTableau10);
  props.products.forEach((product) => {
    const values = DIMENSIONS.map((dimension) => {
      const cell = props.rows.find((row) => row.dimension === dimension.key)?.cells.find((item) => item.product === product);
      return cell?.status === "scored" ? cell.score : null;
    });
    const allScored = values.every((value) => value !== null);
    const line = d3.lineRadial<number | null>()
      .angle((_, index) => angle(index) + Math.PI / 2)
      .radius((value) => radius * Number(value) / 100)
      .defined((value) => value !== null)
      .curve(allScored ? d3.curveLinearClosed : d3.curveLinear);
    group.append("path").datum(values).attr("d", line)
      .attr("fill", allScored ? colors(product) : "none")
      .attr("fill-opacity", 0.08).attr("stroke", colors(product)).attr("stroke-width", 2);
    values.forEach((value, index) => {
      if (value === null) return;
      const coords = point(index, value);
      group.append("circle").attr("cx", coords[0]).attr("cy", coords[1]).attr("r", 3.5)
        .attr("fill", colors(product))
        .on("mouseenter", (event) => {
          if (!tooltip) return;
          tooltip.textContent = `${product} · ${DIMENSIONS[index].code}: ${value}`;
          tooltip.style.display = "block";
          tooltip.style.left = `${event.clientX + 10}px`;
          tooltip.style.top = `${event.clientY + 10}px`;
        })
        .on("mouseleave", () => { if (tooltip) tooltip.style.display = "none"; });
    });
  });

  const legend = svg.append("g").attr("transform", "translate(12,14)");
  props.products.forEach((product, index) => {
    const item = legend.append("g").attr("transform", `translate(0,${index * 20})`);
    item.append("circle").attr("r", 5).attr("fill", colors(product));
    item.append("text").attr("x", 10).attr("y", 4).attr("font-size", 12).text(product);
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
watch(() => [props.rows, props.products], draw, { deep: true });
onBeforeUnmount(() => { observer?.disconnect(); tooltip?.remove(); });
</script>

<template><div ref="host" class="chart-box" data-testid="radar-chart" /></template>
