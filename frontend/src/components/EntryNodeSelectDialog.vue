<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import AppDialog from './AppDialog.vue'
import EntryNodePicker from './EntryNodePicker.vue'
import { api } from '../api'
import type { SubNode } from '../types'

const props = defineProps<{ modelValue: boolean, subscriptionId: string, selectedKeys: string[], nodes: SubNode[] }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: [keys: string[]]
  refreshed: [nodes: SubNode[]]
}>()
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const draft = ref<string[]>([])
const nodes = ref<SubNode[]>([])
const loading = ref(false)
const probing = ref(false)
const error = ref('')
const notice = ref('')
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
const canConfirm = computed(() => !loading.value && draft.value.length > 0 && draft.value.every(key => nodes.value.some(n => n.key === key)))

async function refresh(token = generation) {
  if (token !== generation || !visible.value) return
  clearTimeout(timer)
  error.value = ''
  const result = await api(`/subscriptions/${props.subscriptionId}/nodes`)
  if (token !== generation || !visible.value) return
  nodes.value = (result.nodes as SubNode[]).filter(n => n.included && n.type !== 'chain')
  emit('refreshed', nodes.value)
  const keys = new Set(nodes.value.map(n => n.key))
  const remaining = draft.value.filter(key => keys.has(key))
  if (remaining.length !== draft.value.length) notice.value = '部分已选节点已不符合来源订阅的筛选条件，已移除，请重新检查选择。'
  draft.value = remaining
  probing.value = !!result.job_id
  if (probing.value) timer = setTimeout(() => refresh(token).catch(e => {
    if (token === generation) { error.value = e.message; probing.value = false }
  }), 2000)
}

watch(() => [props.modelValue, props.subscriptionId] as const, async ([open]) => {
  const token = ++generation
  clearTimeout(timer)
  if (!open) return
  draft.value = [...props.selectedKeys]
  nodes.value = [...props.nodes]
  error.value = ''; notice.value = ''; probing.value = false; loading.value = true
  try { await refresh(token) } catch (e) {
    if (token === generation) error.value = e instanceof Error ? e.message : '节点加载失败'
  } finally { if (token === generation) loading.value = false }
})

async function probe(selected: boolean) {
  if (loading.value || probing.value) return
  const keys = selected ? [...draft.value] : nodes.value.map(n => n.key)
  if (!keys.length) return
  const token = generation
  probing.value = true; error.value = ''; notice.value = ''
  try {
    await api(`/subscriptions/${props.subscriptionId}/probe`, 'POST', { keys })
    if (token !== generation || !visible.value) return
    await refresh(token)
  } catch (e) {
    if (token === generation) {
      error.value = e instanceof Error ? e.message : '测速失败，请重试'
      probing.value = false
    }
  }
}
function confirm() {
  if (!canConfirm.value) return
  emit('confirm', [...draft.value])
  visible.value = false
}
onUnmounted(() => { ++generation; clearTimeout(timer) })
</script>

<template>
  <AppDialog v-model="visible" title="选择入口节点" size="960px">
    <p class="muted">勾选候选节点后点击“确认选择”。测速显示服务器经入口节点访问检测地址的延迟；保存链路后会再按经过出口的完整链路自动择优。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb" />
    <el-alert v-if="notice" :title="notice" type="warning" :closable="false" class="mb" />
    <div class="toolbar-actions mb">
      <el-button :disabled="loading || probing || !draft.length" @click="probe(true)">测速已选 ({{ draft.length }})</el-button>
      <el-button :disabled="loading || probing || !nodes.length" @click="probe(false)">测速全部 ({{ nodes.length }})</el-button>
      <el-button :disabled="loading || probing" @click="refresh().catch(e => error = e.message)">刷新结果</el-button>
      <span v-if="probing" role="status" class="muted">正在测速，结果自动更新…</span>
    </div>
    <EntryNodePicker v-if="visible" v-model="draft" :nodes="nodes" :loading="loading" :source-selected="!!subscriptionId" />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!canConfirm" @click="confirm">确认选择 ({{ draft.length }})</el-button>
    </template>
  </AppDialog>
</template>
