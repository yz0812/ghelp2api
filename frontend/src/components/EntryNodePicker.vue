<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import type { SubNode } from '../types'
import { time } from '../utils'
import { regionName } from '../regions'
import RegionBadge from './RegionBadge.vue'

const props = defineProps<{ modelValue: string[], nodes: SubNode[], loading: boolean, sourceSelected: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const query = ref('')
const page = ref(1)
const pageSize = 6
const sort = ref('source')
const region = ref('')
const regions = computed(() => {
  const groups = new Map<string, { code: string, label: string, count: number }>()
  for (const node of props.nodes) {
    const code = node.country || 'UN'
    const group = groups.get(code) || { code, label: regionName(node.country_label || code), count: 0 }
    group.count++
    groups.set(code, group)
  }
  return [...groups.values()].sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
})
const selected = computed(() => props.nodes.filter(n => props.modelValue.includes(n.key)))
const filtered = computed(() => {
  const term = query.value.trim().toLowerCase()
  const result = props.nodes.filter(n => (!region.value || (n.country || 'UN') === region.value) && [n.name, n.country_label, n.country, n.server, n.type, n.group].join(' ').toLowerCase().includes(term))
  if (sort.value === 'latency') result.sort((a, b) => {
    const latency = (n: SubNode) => n.reachable === false || n.latency == null ? Infinity : n.latency
    return latency(a) - latency(b)
  })
  return result
})
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
function toggle(key: string) {
  emit('update:modelValue', props.modelValue.includes(key) ? props.modelValue.filter(k => k !== key) : [...props.modelValue, key])
}
watch([query, sort, region], () => { page.value = 1 })
watch(() => filtered.value.length, length => { page.value = Math.min(page.value, Math.max(1, Math.ceil(length / pageSize))) })
function status(node: SubNode) {
  if (node.reachable === false) return '不可用'
  if (node.latency != null) return node.latency + ' ms'
  return node.reachable === true ? '可连接 · 未测速' : '未测速'
}
</script>

<template>
  <section class="entry-node-picker" aria-label="固定入口节点" :aria-busy="loading">
    <div class="node-picker-toolbar">
      <el-input v-model="query" :prefix-icon="Search" clearable placeholder="搜索名称、地区、协议或服务器" aria-label="搜索入口节点" :disabled="loading || !sourceSelected" />
      <span class="muted">{{ filtered.length }} / {{ nodes.length }} 个节点</span>
      <el-select v-model="sort" aria-label="节点排序" class="node-picker-sort"><el-option value="source" label="订阅顺序" /><el-option value="latency" label="延迟从低到高" /></el-select>
    </div>
    <div class="node-picker-regions" role="group" aria-label="按地区筛选">
      <button type="button" :aria-pressed="region === ''" @click="region = ''">全部地区 ({{ nodes.length }})</button>
      <button v-for="item in regions" :key="item.code" type="button" :aria-pressed="region === item.code" @click="region = item.code"><RegionBadge :country="item.code" :label="item.label" /> ({{ item.count }})</button>
    </div>
    <div class="node-picker-selection" role="status">
      <p>{{ selected.length ? `已选 ${selected.length} 个节点，自动使用完整链路延迟最低的可用入口。` : '请至少选择一个节点；多选后自动测速择优。' }}</p>
      <div v-if="selected.length" class="node-picker-tags">
        <el-tag v-for="node in selected" :key="node.key" closable @close="toggle(node.key)">{{ node.name }}</el-tag>
        <el-button text size="small" @click="emit('update:modelValue', [])">清空选择</el-button>
      </div>
    </div>
    <el-skeleton v-if="loading" :rows="3" animated />
    <p v-else-if="!sourceSelected" class="node-picker-empty">请先选择入口来源订阅。</p>
    <p v-else-if="!nodes.length" class="node-picker-empty">此订阅暂无可选入口节点，请检查订阅来源和筛选条件。</p>
    <div v-else-if="!filtered.length" class="node-picker-empty">
      <p>没有匹配的节点，试试其他名称或地区。</p>
      <el-button @click="query = ''; region = ''">清空搜索</el-button>
    </div>
    <div v-else role="group" aria-label="入口节点列表" class="node-picker-list">
      <label v-for="node in visible" :key="node.key" class="node-picker-row" :class="{ selected: modelValue.includes(node.key) }">
        <input type="checkbox" :value="node.key" :checked="modelValue.includes(node.key)" :aria-label="node.name" @change="toggle(node.key)" />
        <span class="node-picker-info">
          <strong>{{ node.name }}</strong>
          <span class="node-picker-meta"><RegionBadge :country="node.country" :label="node.country_label || node.country" /> · {{ node.type.toUpperCase() }}<template v-if="node.group"> · {{ node.group }}</template></span>
          <code>{{ node.server.includes(':') ? '[' + node.server + ']' : node.server }}:{{ node.port }}</code>
        </span>
        <span class="node-picker-health">
          <el-tag :type="node.reachable === false ? 'danger' : node.latency != null || node.reachable === true ? 'success' : 'info'">{{ status(node) }}</el-tag>
          <small>上次检测：{{ time(node.tested_at) }}</small>
        </span>
      </label>
    </div>
    <div v-if="filtered.length > pageSize && !loading" class="node-picker-pages">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="filtered.length" layout="prev, pager, next" :pager-count="5" small />
    </div>
  </section>
</template>

<style scoped>
.entry-node-picker { width: 100%; min-width: 0; }
.node-picker-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.node-picker-toolbar .el-input { flex: 1 1 240px; }
.node-picker-toolbar .node-picker-sort { width: 160px; }
.node-picker-regions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; max-height: 140px; overflow-y: auto; padding: 2px; }
.node-picker-regions button { border: 1px solid var(--line); border-radius: 6px; background: var(--surface-soft); color: var(--ink); padding: 7px 10px; cursor: pointer; font: inherit; font-size: 12px; }
.node-picker-regions button[aria-pressed="true"] { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
.node-picker-regions button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.node-picker-selection { margin: 12px 0; padding: 10px 12px; background: var(--accent-soft); color: var(--accent); border-radius: 8px; font-size: 12px; overflow-wrap: anywhere; }
.node-picker-selection p { margin: 0; }
.node-picker-tags { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; }
.node-picker-tags .el-tag { max-width: 100%; height: auto; white-space: normal; padding: 4px 8px; }
.node-picker-tags :deep(.el-tag__content) { min-width: 0; overflow-wrap: anywhere; }
.node-picker-list { display: grid; gap: 8px; }
.node-picker-row { display: flex; gap: 12px; align-items: flex-start; padding: 12px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; line-height: 1.6; }
.node-picker-row:hover { background: var(--surface-soft); }
.node-picker-row.selected { border-color: var(--accent); background: var(--accent-soft); }
.node-picker-row input { accent-color: var(--accent); width: 16px; height: 16px; flex: none; margin: 3px 0 0; }
.node-picker-info { display: grid; flex: 1; min-width: 0; gap: 4px; overflow-wrap: anywhere; }
.node-picker-info strong { font-size: 13px; font-weight: 600; }
.node-picker-meta, .node-picker-info code, .node-picker-health small { color: var(--muted); font-size: 12px; }
.node-picker-health { display: flex; flex-direction: column; gap: 6px; align-items: flex-end; }
.node-picker-empty { padding: 20px 12px; text-align: center; color: var(--muted); font-size: 13px; }
.node-picker-pages { display: flex; justify-content: center; margin-top: 12px; }
@media (max-width: 767px) {
  .node-picker-row { flex-wrap: wrap; gap: 8px; }
  .node-picker-health { flex-basis: 100%; padding-left: 24px; align-items: flex-start; }
}
</style>
