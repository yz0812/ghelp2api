<script setup lang="ts">
import { computed } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { jobs } from '../store'

defineProps<{ running: boolean }>()

const active = computed(() => jobs.value.filter(j => ['pending', 'running'].includes(j.status)))
const percentage = computed(() =>
  Math.round(active.value.reduce((a, j) => a + j.completed, 0) / Math.max(1, active.value.reduce((a, j) => a + j.total, 0)) * 100))
</script>

<template>
  <div v-if="running && active.length" class="running-banner" role="status">
    <el-icon class="is-loading"><Refresh /></el-icon>
    <span>{{ active.length }} 个任务进行中</span>
    <el-progress :percentage="percentage" :stroke-width="6" />
    <router-link class="text-link" :to="{ name: 'jobs' }">查看进度 →</router-link>
  </div>
</template>
