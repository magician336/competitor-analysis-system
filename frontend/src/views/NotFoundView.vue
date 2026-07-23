<script setup lang="ts">
import { useRouter } from "vue-router";
const router = useRouter();
</script>

<template>
  <el-result icon="warning" title="页面不存在" sub-title="请从左侧导航选择一个有效页面">
    <template #extra><el-button type="primary" @click="router.push('/overview')">返回系统概览</el-button></template>
  </el-result>
</template>
