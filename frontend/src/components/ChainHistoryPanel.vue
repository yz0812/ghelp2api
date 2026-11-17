<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { time } from '../utils'

const props = defineProps<{ chainId: string; active: boolean }>()
const range = ref('24h'), history = ref<any>(null), error = ref(''), loading = ref(false)
let generation = 0
const events: Record<string, string> = { failure: '检测失败', recovered: '检测恢复', ip_changed: '出口 IP 变化', path_changed: '运行路径切换' }
const chart = computed(() => {
  const values = (history.value?.series || []) as { at: number; latency: number | null }[]
  if (!values.length) return []
  const max = Math.max(1, ...values.map(v => v.latency || 0)), start = values[0]!.at, span = Math.max(1, values.at(-1)!.at - start)
  const paths: string[] = []; let points: string[] = []; let previous: number | undefined
  for (const value of values) {
    if (value.latency == null || (previous && value.at - previous > history.value.bucket_seconds * 2)) {
      if (points.length) paths.push(points.join(' ')); points = []
    }
    if (value.latency != null) points.push(`${20 + (value.at - start) / span * 660},${150 - value.latency / max * 125}`)
    previous = value.at
  }
  if (points.length) paths.push(points.join(' '))
  return paths
})
const peak = computed(() => Math.max(0, ...((history.value?.series || []) as { latency: number | null }[]).map(v => v.latency || 0)))
async function refresh() {
  const token = ++generation
  loading.value = true; error.value = ''
  try {
    const value = await api(`/chains/${props.chainId}/history?range=${range.value}`)
    if (token === generation) history.value = value
  } catch (e) { if (token === generation) error.value = (e as Error).message } finally { if (token === generation) loading.value = false }
}
async function moreEvents() {
  const next = history.value?.next_offset
  if (next == null) return
  try {
    const value = await api(`/chains/${props.chainId}/history?range=${range.value}&offset=${next}`)
    history.value.events.push(...value.events); history.value.next_offset = value.next_offset
  } catch (e) { error.value = (e as Error).message }
}
watch(() => [props.active, props.chainId, range.value] as const, ([active]) => { if (active) refresh() }, { immediate: true })
defineExpose({ refresh })
</script>

<template>
  <div class="history">
    <div class="toolbar-actions history-head">
      <el-radio-group v-model="range" aria-label="时间范围"><el-radio-button value="24h">24 小时</el-radio-button><el-radio-button value="7d">7 天</el-radio-button><el-radio-button value="30d">30 天</el-radio-button></el-radio-group>
      <el-button :loading="loading" @click="refresh">刷新历史</el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb" />
    <el-skeleton v-if="!history && loading" :rows="4" animated />
    <template v-if="history">
      <div class="history-stats">
        <div><span>自动监控</span><strong>{{ history.monitor_enabled ? '自动监控已开启' : '自动监控未开启' }}</strong></div>
        <div><span>检测次数</span><strong>{{ history.sample_count }}</strong></div>
        <div><span>监控可用率</span><strong>{{ history.availability == null ? '暂无数据' : history.availability + '%' }}</strong></div>
      </div>
      <figure class="history-figure">
        <figcaption><span>完整路径延迟</span><small v-if="peak">峰值 {{ peak }} ms</small></figcaption>
        <svg v-if="chart.length" class="history-chart" viewBox="0 0 700 160" role="img" aria-label="完整路径延迟趋势，空白表示无有效测量"><line x1="20" y1="150" x2="680" y2="150" stroke="var(--line)" /><line x1="20" y1="25" x2="680" y2="25" stroke="var(--line)" stroke-dasharray="4 6" /><polyline v-for="(points, i) in chart" :key="i" :points="points" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" /><circle v-for="(points, i) in chart.filter(p => !p.includes(' '))" :key="'point' + i" :cx="points.split(',')[0]" :cy="points.split(',')[1]" r="4" fill="var(--accent)" /></svg>
        <p v-else class="muted chart-empty">尚无可绘制的延迟数据。</p>
      </figure>
      <p class="field-hint">可用率只统计已完成的自动监控样本，缺测不算成功；趋势包含手动检测。</p>
      <h3 class="history-title">运行事件</h3>
      <p v-if="!history.events.length" class="muted">此时间范围内暂无事件。</p>
      <ul v-else class="history-events">
        <li v-for="event in history.events" :key="event.id" :class="'is-' + event.kind">
          <strong>{{ events[event.kind] || event.kind }}</strong>
          <span>{{ event.detail.error || (event.kind === 'ip_changed' ? event.detail.before + ' → ' + event.detail.after : '') }}</span>
          <time>{{ time(event.at) }}</time>
        </li>
      </ul>
      <el-button v-if="history.next_offset != null" class="mt" @click="moreEvents">加载更多事件</el-button>
    </template>
  </div>
</template>

<style scoped>
.history-head { justify-content: space-between; margin-bottom: 16px; }
.history-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.history-stats > div { display: flex; flex-direction: column; gap: 6px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); min-width: 0; }
.history-stats span { color: var(--muted); font-size: 12px; }
.history-stats strong { font-size: 14px; overflow-wrap: anywhere; }
.history-figure { margin: 0; padding: 14px; border: 1px solid var(--line); border-radius: 10px; }
.history-figure figcaption { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.history-chart { display: block; width: 100%; height: auto; max-height: 200px; }
.chart-empty { margin: 24px 0; text-align: center; }
.history-title { margin: 20px 0 8px; }
.history-events { margin: 0; padding: 0; list-style: none; }
.history-events li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; padding: 10px 0 10px 12px; border-left: 2px solid var(--line); font-size: 13px; overflow-wrap: anywhere; }
.history-events li.is-failure { border-left-color: var(--danger); }
.history-events li.is-recovered { border-left-color: var(--success); }
.history-events li.is-ip_changed, .history-events li.is-path_changed { border-left-color: var(--warning); }
.history-events span { flex: 1 1 160px; color: var(--muted); }
.history-events time { color: var(--subtle); font-size: 12px; }
@media (max-width: 600px) { .history-stats { grid-template-columns: 1fr; } }
</style>
