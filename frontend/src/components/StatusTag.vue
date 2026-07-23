<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ status: string }>();
const type = computed(() => {
  if (["success", "ready", "green", "scored", "table_stakes"].includes(props.status)) return "success";
  if (["running", "queued", "cancelling", "orange"].includes(props.status)) return "warning";
  if (["failed", "timed_out", "red"].includes(props.status)) return "danger";
  return "info";
});
const label = computed(() => ({
  queued: "排队中", running: "运行中", cancelling: "取消中", cancelled: "已取消",
  success: "成功", partial_failure: "部分失败", failed: "失败", timed_out: "已超时",
  pending: "等待中", skipped: "已跳过", scored: "已评分",
  insufficient_evidence: "N/A", table_stakes: "行业标配",
  not_table_stakes: "非行业标配", insufficient_data: "数据不足",
  ready: "就绪", green: "正常", orange: "橙色", red: "红色", yellow: "黄色"
}[props.status] || props.status));
</script>

<template><el-tag :type="type" effect="plain">{{ label }}</el-tag></template>
