<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import RegionBadge from './RegionBadge.vue'
import { regionName } from '../regions'
import { latencyClass } from '../utils'
import type { SubNode } from '../types'

/** 单选或多选节点列表。多选时按点击顺序记录，用于主备候选优先级。 */
const props = defineProps<{ modelValue: string[]; nodes: SubNode[]; multiple?: boolean; loading?: boolean; label: string; disabled?: (node: SubNode) => string | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const limit = 200
const query = ref(''), region = ref(''), protocol = ref('')
const regions = computed(() => {
  const groups = new Map<string, string>()
  for (const node of props.nodes) groups.set(node.country || 'UN', regionName(node.country_label || node.country))
  return [...groups].map(([code, label]) => ({ code, label })).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
})
const protocols = computed(() => [...new Set(props.nodes.map(n => n.type))].sort())
const filtered = computed(() => {
  const term = query.value.trim().toLowerCase()
  return props.nodes.filter(n => (!region.value || (n.country || 'UN') === region.value) && (!protocol.value || n.type === protocol.value) &&
    [n.name, n.server, n.country_label, n.type].join(' ').toLowerCase().includes(term))
})
watch(() => props.nodes, () => { if (region.value && !regions.value.some(r => r.code === region.value)) region.value = '' })
function reason(node: SubNode) { return !node.included ? (node.reason || '已被订阅筛选排除') : props.disabled?.(node) || null }
function order(node: SubNode) { return props.modelValue.indexOf(node.key) + 1 }
function choose(node: SubNode) {
  if (reason(node)) return
  if (!props.multiple) return emit('update:modelValue', [node.key])
  emit('update:modelValue', props.modelValue.includes(node.key) ? props.modelValue.filter(k => k !== node.key) : [...props.modelValue, node.key])
}
</script>

<template>
  <div class="node-list">
    <div class="node-filters">
      <el-input v-model="query" :prefix-icon="Search" clearable placeholder="搜索名称、地区或地址" aria-label="筛选节点" />
      <el-select v-model="region" clearable placeholder="全部地区" aria-label="节点地区"><el-option v-for="r in regions" :key="r.code" :label="r.label" :value="r.code" /></el-select>
      <el-select v-model="protocol" clearable placeholder="全部协议" aria-label="节点协议"><el-option v-for="p in protocols" :key="p" :label="p" :value="p" /></el-select>
    </div>
    <div class="node-scroll" role="listbox" :aria-label="label" :aria-multiselectable="multiple || undefined" :aria-busy="loading || undefined">
      <p v-if="loading && !nodes.length" class="muted node-empty">正在加载节点…</p>
      <p v-else-if="!filtered.length" class="muted node-empty">{{ nodes.length ? '没有符合筛选条件的节点' : '该来源暂无可用节点' }}</p>
      <button v-for="node in filtered.slice(0, limit)" :key="node.key" type="button" role="option" :aria-selected="modelValue.includes(node.key)" :aria-disabled="!!reason(node) || undefined" :class="['node-item', { selected: modelValue.includes(node.key), disabled: reason(node) }]" @click="choose(node)">
        <span class="node-mark" aria-hidden="true">{{ multiple && order(node) ? order(node) : '' }}</span>
        <span class="node-text">
          <strong>{{ node.name }}</strong>
          <small><RegionBadge :country="node.country" :label="node.country_label" /><span>{{ node.type }}</span><span v-if="reason(node)" class="danger-text">{{ reason(node) }}</span></small>
        </span>
        <span :class="['node-latency', latencyClass(node)]">{{ node.reachable === false ? '不可用' : node.latency != null ? node.latency + ' ms' : '未测速' }}</span>
      </button>
    </div>
    <p v-if="filtered.length > limit" class="field-hint">仅显示前 {{ limit }} 个，共 {{ filtered.length }} 个，请输入关键字缩小范围。</p>
  </div>
</template>

<style scoped>
.node-filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.node-filters > .el-input { flex: 2 1 180px; }
.node-filters > .el-select { flex: 1 1 110px; }
.node-scroll { max-height: 320px; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--line); border-radius: 10px; }
.node-empty { margin: 0; padding: 24px; text-align: center; }
.node-item { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 12px; border: 0; border-bottom: 1px solid var(--line); background: var(--surface); color: var(--ink); text-align: left; }
.node-item:last-child { border-bottom: 0; }
.node-item:hover { background: var(--surface-soft); }
.node-item.selected { background: var(--accent-soft); }
.node-item.disabled { cursor: not-allowed; opacity: .55; }
.node-mark { display: grid; place-items: center; flex: none; width: 18px; height: 18px; border: 1.5px solid var(--line); border-radius: 50%; font-size: 11px; font-weight: 650; color: #fff; }
.node-item.selected .node-mark { border-color: var(--accent); background: var(--accent); }
html.dark .node-item.selected .node-mark { color: #12213c; }
.node-text { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.node-text strong { font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }
.node-text small { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; color: var(--muted); font-size: 12px; }
.node-latency { flex: none; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--muted); }
</style>
