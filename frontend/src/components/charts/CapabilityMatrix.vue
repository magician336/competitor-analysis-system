<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as d3 from "d3";
import { DIMENSIONS, type MatrixRow } from "../../types/api";

const props = defineProps<{ rows: MatrixRow[]; products: string[] }>();
const host = ref<HTMLDivElement | null>(null);
let observer: ResizeObserver | null = null;
let tooltip: HTMLDivElement | null = null;

const palette = {
  ink: "#213548",
  muted: "#657580",
  accent: "#356ca3",
  paper: "#f4f1ea",
  missing: "rgba(60,67,79,.055)"
};

function scoreColor(score: number) {
  if (score >= 90) return "#245b91";
  if (score >= 85) return "#3f76aa";
  if (score >= 80) return "#6898c4";
  if (score >= 75) return "#94b5d3";
  if (score >= 70) return "#bdd1e2";
  return "#e1eaf2";
}

function draw() {
  if (!host.value) return;
  const root = d3.select(host.value);
  root.selectAll("*").remove();

  const width = Math.max(host.value.clientWidth, 680);
  const left = 174;
  const top = 50;
  const cellHeight = 56;
  const dataWidth = width - left - 8;
  const cellWidth = Math.max(92, dataWidth / Math.max(props.products.length, 1));
  const matrixWidth = cellWidth * props.products.length;
  const matrixHeight = DIMENSIONS.length * cellHeight;
  const height = top + matrixHeight + 16;

  const legend = root.append("div").attr("class", "capability-matrix__legend");
  legend.append("strong").text("能力得分");
  [
    { label: "<70", color: "#e1eaf2" },
    { label: "70–74", color: "#bdd1e2" },
    { label: "75–79", color: "#94b5d3" },
    { label: "80–84", color: "#6898c4" },
    { label: "85–89", color: "#3f76aa" },
    { label: "≥90", color: "#245b91" }
  ].forEach((item) => {
    const entry = legend.append("span");
    entry.append("i").style("background-color", item.color);
    entry.append("b").text(item.label);
  });

  const svg = root.append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("role", "img")
    .attr("aria-label", "五个竞品在七个能力维度上的蓝色能力评分矩阵");
  svg.append("title").text("最新能力矩阵");

  props.products.forEach((product, index) => {
    const x = left + index * cellWidth;
    svg.append("rect").attr("x", x + 3).attr("y", 4).attr("width", cellWidth - 6).attr("height", 32)
      .attr("fill", "rgba(251,250,246,.58)").attr("stroke", "rgba(25,31,42,.2)");
    svg.append("rect").attr("x", x + 11).attr("y", 17).attr("width", 5).attr("height", 5)
      .attr("fill", palette.accent).attr("fill-opacity", .72);
    svg.append("text").attr("x", x + cellWidth / 2 + 6).attr("y", 25)
      .attr("text-anchor", "middle").attr("font-size", 13)
      .attr("font-family", "Source Serif 4, STZhongsong, serif").attr("fill", palette.ink)
      .text(product);
  });

  svg.append("rect").attr("x", left).attr("y", top).attr("width", matrixWidth).attr("height", matrixHeight)
    .attr("fill", "none").attr("stroke", "rgba(33,53,72,.34)");

  DIMENSIONS.forEach((dimension, rowIndex) => {
    const y = top + rowIndex * cellHeight;
    svg.append("text").attr("x", 4).attr("y", y + 35)
      .attr("fill", palette.accent).attr("font-size", 10).attr("font-family", "ui-monospace, monospace")
      .text(dimension.code);
    svg.append("text").attr("x", 35).attr("y", y + 35)
      .attr("font-size", 14).attr("font-family", "Source Serif 4, STZhongsong, serif")
      .attr("fill", palette.ink).text(dimension.name);
    svg.append("line").attr("x1", 4).attr("x2", left - 14).attr("y1", y + cellHeight).attr("y2", y + cellHeight)
      .attr("stroke", "rgba(25,31,42,.13)");

    props.products.forEach((product, columnIndex) => {
      const cell = props.rows.find((row) => row.dimension === dimension.key)?.cells.find((item) => item.product === product);
      const scored = cell?.status === "scored" && cell.score !== null;
      const score = scored ? Number(cell?.score) : null;
      const x = left + columnIndex * cellWidth;
      const group = svg.append("g").attr("transform", `translate(${x},${y})`);
      const fill = score === null ? palette.missing : scoreColor(score);
      const lightInk = score !== null && score >= 85;

      group.append("rect")
        .attr("x", 3).attr("y", 3).attr("width", cellWidth - 6).attr("height", cellHeight - 6)
        .attr("fill", fill)
        .attr("stroke", score === null ? "rgba(60,67,79,.2)" : "rgba(33,53,72,.18)")
        .attr("stroke-dasharray", score === null ? "3 3" : null)
        .on("mouseenter", (event) => {
          if (!tooltip) return;
          tooltip.textContent = scored
            ? `${product} · ${dimension.code}: ${cell?.score}（置信度 ${Math.round((cell?.confidence || 0) * 100)}% · ${cell?.evidence_count || 0} 条证据）`
            : `${product} · ${dimension.code}: N/A（证据不足）`;
          tooltip.style.display = "block";
          tooltip.style.left = `${event.clientX + 10}px`;
          tooltip.style.top = `${event.clientY + 10}px`;
        })
        .on("mouseleave", () => { if (tooltip) tooltip.style.display = "none"; });

      group.append("rect").attr("x", 10).attr("y", 10).attr("width", 5).attr("height", 5)
        .attr("fill", lightInk ? "#f4f1ea" : palette.accent).attr("fill-opacity", lightInk ? .72 : .55);
      group.append("path")
        .attr("d", `M${cellWidth - 21},${cellHeight - 11} H${cellWidth - 11} V${cellHeight - 21}`)
        .attr("fill", "none")
        .attr("stroke", lightInk ? "rgba(244,241,234,.66)" : "rgba(53,108,163,.45)")
        .attr("stroke-width", 1);
      group.append("text").attr("x", cellWidth / 2).attr("y", 36).attr("text-anchor", "middle")
        .attr("fill", lightInk ? "#fff" : palette.ink)
        .attr("font-size", 18).attr("font-family", "Source Serif 4, STZhongsong, serif")
        .attr("font-weight", 500).text(score === null ? "N/A" : score);
    });
  });

  const cornerPoints = [
    [left, top], [left + matrixWidth, top],
    [left, top + matrixHeight], [left + matrixWidth, top + matrixHeight]
  ];
  cornerPoints.forEach(([x, y]) => {
    svg.append("rect").attr("x", x - 3).attr("y", y - 3).attr("width", 6).attr("height", 6)
      .attr("fill", palette.paper).attr("stroke", palette.accent).attr("stroke-width", .8);
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

<template><div ref="host" class="chart-box capability-matrix" data-testid="capability-matrix" /></template>
