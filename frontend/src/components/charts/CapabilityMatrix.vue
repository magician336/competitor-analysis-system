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
  const cellHeight = 46;
  const left = 158;
  const top = 100;
  const width = Math.max(host.value.clientWidth, 600);
  const cellWidth = Math.max(82, (width - left - 12) / Math.max(props.products.length, 1));
  const height = top + DIMENSIONS.length * cellHeight + 20;
  const root = d3.select(host.value);
  root.selectAll("*").remove();
  const svg = root.append("svg").attr("viewBox", `0 0 ${width} ${height}`).attr("role", "img");
  const color = d3.scaleSequential((value) => d3.interpolateRgbBasis(["#e8eefb", "#77b7f7", "#7368e8"])(value)).domain([0, 100]);
  const defs = svg.append("defs");
  const gradient = defs.append("linearGradient").attr("id", "matrix-legend");
  gradient.append("stop").attr("offset", "0%").attr("stop-color", "#e8eefb");
  gradient.append("stop").attr("offset", "50%").attr("stop-color", "#77b7f7");
  gradient.append("stop").attr("offset", "100%").attr("stop-color", "#7368e8");
  svg.append("rect").attr("x", left).attr("y", 8).attr("width", 118).attr("height", 7).attr("fill", "url(#matrix-legend)");
  svg.append("text").attr("x", left).attr("y", 33).attr("font-size", 12).attr("fill", "#6f7280").text("0");
  svg.append("text").attr("x", left + 118).attr("y", 33).attr("text-anchor", "end").attr("font-size", 12).attr("fill", "#6f7280").text("100 · 能力分数");
  svg.append("text").attr("x", left + 148).attr("y", 18).attr("font-size", 12).attr("fill", "#6f7280").text("N/A = 证据不足");

  props.products.forEach((product, index) => {
    svg.append("text").attr("x", left + index * cellWidth + cellWidth / 2).attr("y", 70)
      .attr("text-anchor", "middle").attr("font-size", 14).attr("font-family", "Source Serif 4, STZhongsong, serif").text(product);
  });
  DIMENSIONS.forEach((dimension, rowIndex) => {
    svg.append("text").attr("x", 8).attr("y", top + rowIndex * cellHeight + 26)
      .attr("font-size", 14).attr("font-family", "Source Serif 4, STZhongsong, serif").text(`${dimension.code} ${dimension.name}`);
    props.products.forEach((product, columnIndex) => {
      const cell = props.rows.find((row) => row.dimension === dimension.key)?.cells.find((item) => item.product === product);
      const scored = cell?.status === "scored" && cell.score !== null;
      const group = svg.append("g").attr("transform", `translate(${left + columnIndex * cellWidth},${top + rowIndex * cellHeight})`);
      group.append("rect").attr("width", cellWidth - 2).attr("height", cellHeight - 2).attr("rx", 0)
        .attr("fill", scored ? color(cell.score as number) : "rgba(60, 67, 79, .06)")
        .attr("stroke", scored ? "rgba(255,255,255,.45)" : "rgba(60,67,79,.16)")
        .attr("stroke-dasharray", scored ? null : "3 3")
        .on("mouseenter", (event) => {
          if (!tooltip) return;
          tooltip.textContent = scored
            ? `${product} · ${dimension.code}: ${cell?.score}（置信度 ${Math.round((cell?.confidence || 0) * 100)}%）`
            : `${product} · ${dimension.code}: N/A（证据不足）`;
          tooltip.style.display = "block";
          tooltip.style.left = `${event.clientX + 10}px`;
          tooltip.style.top = `${event.clientY + 10}px`;
        })
        .on("mouseleave", () => { if (tooltip) tooltip.style.display = "none"; });
      group.append("text").attr("x", (cellWidth - 4) / 2).attr("y", 27).attr("text-anchor", "middle")
        .attr("fill", scored && Number(cell?.score) > 58 ? "#fff" : "#303133")
        .attr("font-size", 15).attr("font-family", "Source Serif 4, STZhongsong, serif").text(scored ? String(cell?.score) : "N/A");
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
watch(() => [props.rows, props.products], draw, { deep: true });
onBeforeUnmount(() => { observer?.disconnect(); tooltip?.remove(); });
</script>

<template><div ref="host" class="chart-box" data-testid="capability-matrix" /></template>
