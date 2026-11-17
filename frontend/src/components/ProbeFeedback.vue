<script setup lang="ts">
import { time } from '../utils'
import type { ProbeResult } from '../types'
defineProps<{ loading?: boolean, result?: ProbeResult | null }>()
</script>

<template>
  <div v-if="loading || result" class="probe-feedback" role="status" aria-live="polite">
    <span v-if="loading" class="muted">正在检测，请稍候…</span>
    <template v-else-if="result">
      <strong :class="result.reachable ? 'latency-good' : 'danger-text'">{{ result.reachable ? `最近检测可用 · ${result.latency} ms` : '最近检测失败' }}</strong>
      <div v-if="result.entry">检测时最快入口：{{ result.entry }}</div>
      <div v-if="result.error" class="danger-text">{{ result.error }}</div>
      <small class="muted">检测时间：{{ time(result.tested_at) }}</small>
    </template>
  </div>
</template>

<style scoped>
.probe-feedback { padding: 10px 12px; margin-top: 10px; border: 1px solid var(--line); background: var(--surface-soft); border-radius: 8px; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
</style>
