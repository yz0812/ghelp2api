<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Plus, Refresh, Search, Collection } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import PageHeading from '../components/PageHeading.vue'
import AppDialog from '../components/AppDialog.vue'
import EntryNodeSelectDialog from '../components/EntryNodeSelectDialog.vue'
import ActionMenu from '../components/ActionMenu.vue'
import EmptyState from '../components/EmptyState.vue'
import FilterBar from '../components/FilterBar.vue'
import ProbeFeedback from '../components/ProbeFeedback.vue'
import ChainPathPreview from '../components/ChainPathPreview.vue'
import ChainDrawer from '../components/ChainDrawer.vue'
import { api } from '../api'
import { action, busy, chains, load, subscriptions, settings, clearError } from '../store'
import { chainState, connectionText, copyText, relayAddress, strategyLabel, time } from '../utils'
import type { Chain, ChainTemplate, ConnectionInfo, Entry, Exit, MenuAction, SubNode, ProbeResult } from '../types'

type Kind = 'entries' | 'exits' | 'chains'
type Resource = 'entries' | 'exits'
type StatusFilter = 'all' | 'active' | 'issue' | 'off'
type EntryForm = Omit<Entry, 'id'> & { id?: string }
type ExitForm = Omit<Exit, 'id' | 'has_password'> & { id?: string, password: string, content: string }

const router = useRouter()
const tab = ref<Kind>('chains')
const data = reactive<{ entries: Entry[], exits: Exit[], chains: Chain[] }>({ entries: [], exits: [], chains: [] })
const labels: Record<Kind, string> = { chains: '链路', entries: '入口', exits: '出口' }
const initialLoading = ref(true)
const search = ref('')
const statusFilter = ref<StatusFilter>('all')
const matches = (row: { name: string }) => row.name.toLowerCase().includes(search.value.trim().toLowerCase())

// ---- 链路列表 ----
function group(row: Chain): Exclude<StatusFilter, 'all'> {
  const tone = chainState(row).tone
  return tone === 'off' ? 'off' : tone === 'error' || tone === 'warn' ? 'issue' : 'active'
}
const counts = computed(() => {
  const result = { all: data.chains.length, active: 0, issue: 0, off: 0 }
  for (const row of data.chains) result[group(row)]++
  return result
})
const filteredChains = computed(() => data.chains.filter(row => matches(row) && (statusFilter.value === 'all' || group(row) === statusFilter.value)))
const filteredResources = computed(() => tab.value === 'chains' ? [] : (data[tab.value] as { name: string }[]).filter(matches))
const probes = reactive<Record<string, { loading: boolean, result?: ProbeResult }>>({})
function probeResult(kind: 'chains' | 'exits', row: Chain | Exit) {
  const local = probes[kind + ':' + row.id]?.result
  return local && (!row.probe || local.tested_at > row.probe.tested_at) ? local : row.probe
}
function hopsOf(row: Chain) { return row.configured_paths?.[0]?.hops || [] }
function references(source: 'entry' | 'proxy', id: string) {
  return data.chains.filter(c => c.paths?.some(p => p.hops.some(h => h.source === source && h.ref === id))).length
}
function chainActions(row: Chain): MenuAction[] {
  return [
    { key: 'overview', label: '详情与连接信息' },
    { key: 'diagnostic', label: '路径诊断' },
    { key: 'history', label: '监控与事件' },
    { key: 'toggle', label: row.enabled ? '停用链路' : '启用链路', divided: true },
    { key: 'clone', label: '复制链路' },
    { key: 'generate', label: '生成订阅' },
    { key: 'remove', label: '删除链路', danger: true, divided: true }
  ]
}

async function refresh() {
  const [entries, exits, chainList] = await Promise.all([api('/entries'), api('/exits'), api('/chains')])
  Object.assign(data, { entries, exits, chains: chainList })
}
async function reload() { await Promise.all([refresh(), load()]) }
let statusTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false
async function pollStatus() {
  if (disposed) return
  try {
    if (tab.value === 'chains' && !document.hidden && !busy.value) {
      const rows: Chain[] = await api('/chains')
      if (!disposed) data.chains = rows
    }
  } catch {
    for (const row of data.chains) { row.status.current_entry = null; row.status.latency = null }
  } finally {
    if (!disposed) statusTimer = setTimeout(pollStatus, 5000)
  }
}
onMounted(async () => {
  await action(refresh)
  initialLoading.value = false
  if (!disposed) statusTimer = setTimeout(pollStatus, 5000)
})
onUnmounted(() => { disposed = true; clearTimeout(statusTimer) })
watch(chains, value => { data.chains = value })
watch(tab, () => { search.value = '' })

async function probe(kind: 'chains' | 'exits', id: string) {
  const key = kind + ':' + id
  if (probes[key]?.loading) return
  probes[key] = { loading: true }
  try {
    probes[key].result = await api('/' + kind + '/' + id + '/probe', 'POST')
    if (kind === 'chains') await refresh().catch(() => ElMessage.warning('检测已完成，当前入口状态刷新失败，请稍后刷新状态'))
  } catch (e) {
    probes[key].result = { reachable: false, latency: null, error: e instanceof Error ? e.message : '检测失败，请重试', tested_at: Date.now() / 1000 }
  } finally { probes[key].loading = false }
}
function edit(row?: Chain) { router.push(row ? '/chains/' + row.id + '/edit' : '/chains/new') }
async function removeChain(row: Chain) {
  await action(async () => {
    await ElMessageBox.confirm('删除链路「' + row.name + '」？已分发的订阅需要先解除关联。', '删除链路', { type: 'warning', confirmButtonText: '删除' })
    await api('/chains/' + row.id, 'DELETE')
    delete probes['chains:' + row.id]
    selected.value = selected.value.filter(id => id !== row.id)
    await reload()
    ElMessage.success('链路已删除')
  })
}
async function clone(row: Chain) {
  await action(async () => {
    const result = await ElMessageBox.prompt('副本默认停用，并生成新的端口及认证信息。', '复制链路', { inputValue: row.name + ' 副本', inputValidator: v => !!v?.trim() || '请输入名称' })
    await api('/chains/' + row.id + '/clone', 'POST', { name: result.value })
    await reload(); ElMessage.success('链路副本已创建')
  })
}
async function generate(row: Chain) {
  await action(async () => {
    await api('/subscriptions', 'POST', { name: row.name + ' 订阅', chain_ids: [row.id], chain_mode: row.relay_enabled ? 'relay' : 'native' })
    await load()
    router.push('/subscriptions')
  })
}
async function toggle(row: Chain) {
  await action(async () => {
    const [result] = (await api('/chains/batch', 'POST', { operation: row.enabled ? 'disable' : 'enable', ids: [row.id] })).results
    await reload()
    if (result?.ok) ElMessage.success(row.enabled ? '链路已停用' : '链路已启用')
    else ElMessage.error(result?.error || '操作失败')
  })
}
function onChainAction(key: string, row: Chain) {
  if (['overview', 'diagnostic', 'history'].includes(key)) return openDrawer(row, key)
  if (key === 'toggle') return toggle(row)
  if (key === 'clone') return clone(row)
  if (key === 'generate') return generate(row)
  if (key === 'remove') return removeChain(row)
}

// ---- 详情抽屉 ----
const drawer = ref(false), drawerTab = ref('overview'), drawerId = ref('')
const drawerRef = ref<InstanceType<typeof ChainDrawer>>()
const drawerChain = computed(() => data.chains.find(c => c.id === drawerId.value) || null)
function openDrawer(row: Chain, view = 'overview') { drawerId.value = row.id; drawerTab.value = view; drawer.value = true }
async function copyChainConnection(row: Chain) {
  try {
    const info = await api('/chains/' + row.id + '/connection') as ConnectionInfo
    if (!await copyText(connectionText(info))) {
      openDrawer(row); await nextTick()
      drawerRef.value?.showManualCopy(info)
    }
  } catch (e) { ElMessage.error((e as Error).message) }
}

// ---- 批量操作 ----
const selected = ref<string[]>([])
const batchResults = ref<{ id: string; name?: string; ok: boolean; error?: string; job_id?: string }[]>([])
const allSelected = computed(() => filteredChains.value.length > 0 && filteredChains.value.every(r => selected.value.includes(r.id)))
const someSelected = computed(() => !allSelected.value && filteredChains.value.some(r => selected.value.includes(r.id)))
watch(() => data.chains.map(c => c.id).join(), () => { selected.value = selected.value.filter(id => data.chains.some(c => c.id === id)) })
function select(id: string, value: boolean) {
  selected.value = value ? [...new Set([...selected.value, id])] : selected.value.filter(v => v !== id)
}
function selectAll(value: boolean) {
  const ids = filteredChains.value.map(r => r.id)
  selected.value = value ? [...new Set([...selected.value, ...ids])] : selected.value.filter(id => !ids.includes(id))
}
const batchOperations: Record<string, string> = { probe: '批量检测', enable: '启用', disable: '停用', monitor_on: '开启监控', monitor_off: '关闭监控' }
async function batch(operation: string) {
  if (!selected.value.length) return
  await action(async () => {
    if (operation === 'delete') await ElMessageBox.confirm(`删除所选 ${selected.value.length} 条链路？存在订阅引用的链路会保留并报告原因。`, '批量删除', { type: 'warning', confirmButtonText: '删除' })
    batchResults.value = (await api('/chains/batch', 'POST', { operation, ids: selected.value })).results
    if (operation === 'delete') selected.value = selected.value.filter(id => batchResults.value.some(r => r.id === id && !r.ok))
    await reload()
  })
}
const subscriptionDialog = ref(false), subscriptionId = ref(''), subscriptionName = ref('')
const subscriptionMode = ref<'relay' | 'native'>('relay')
function openSubscription() {
  subscriptionId.value = ''
  subscriptionName.value = '链路订阅'
  subscriptionMode.value = 'relay'
  subscriptionDialog.value = true
}
watch(subscriptionId, id => {
  const sub = subscriptions.value.find(s => s.id === id)
  subscriptionMode.value = sub?.chain_ids.length ? sub.chain_mode : 'relay'
})
async function saveSubscription() {
  await action(async () => {
    const ids = selected.value
    if (subscriptionId.value) {
      const sub = subscriptions.value.find(s => s.id === subscriptionId.value)!
      const { id, clash_url: _clash, proxies_url: _proxies, last_probe: _probe, ...values } = sub
      await api('/subscriptions/' + id, 'PUT', { ...values, chain_mode: subscriptionMode.value, chain_ids: [...new Set([...sub.chain_ids, ...ids])] })
    } else await api('/subscriptions', 'POST', { name: subscriptionName.value, chain_mode: subscriptionMode.value, chain_ids: ids })
    subscriptionDialog.value = false; await reload(); ElMessage.success('订阅已保存')
  })
}

// ---- 模板库 ----
const templatesOpen = ref(false), templates = ref<ChainTemplate[]>([])
async function showTemplates() { await action(async () => { templates.value = await api('/chain-templates'); templatesOpen.value = true }) }
async function removeTemplate(row: ChainTemplate) {
  await action(async () => {
    await ElMessageBox.confirm('删除模板「' + row.name + '」？已创建的链路不受影响。', '删除模板', { type: 'warning', confirmButtonText: '删除' })
    await api('/chain-templates/' + row.id, 'DELETE'); templates.value = await api('/chain-templates')
  })
}

// ---- 入口与出口资源 ----
const dialog = ref(false)
const editing = ref<Resource>('entries')
const entryForm = reactive<EntryForm>({ name: '', subscription_id: '', mode: 'auto', node_key: null, node_keys: [], enabled: true })
const exitForm = reactive<ExitForm>({ name: '', type: 'http', server: '', port: 80, username: '', password: '', tls: false, content: '', enabled: true })
const activeForm = computed(() => editing.value === 'entries' ? entryForm : exitForm)
const inputMode = ref<'manual' | 'yaml'>('manual')
const nodes = ref<SubNode[]>([])
const nodesLoading = ref(false)
const nodePickerVisible = ref(false)
const selectedNodeNames = computed(() => nodes.value.filter(n => entryForm.node_keys.includes(n.key)).map(n => n.name).join('、'))
const eligible = computed(() => subscriptions.value.filter(s => s.account_ids.length || s.source_ids.length))
let nodeRequest = 0
watch(dialog, open => { if (!open) nodePickerVisible.value = false })
const canSave = computed(() => {
  if (!activeForm.value.name.trim()) return false
  if (editing.value === 'entries') return !!entryForm.subscription_id && (entryForm.mode === 'auto' || (!nodesLoading.value && entryForm.node_keys.length > 0 && entryForm.node_keys.every(key => nodes.value.some(n => n.key === key))))
  return inputMode.value === 'yaml' ? !!exitForm.content.trim() : !!exitForm.server.trim() && !!exitForm.port
})
function editResource(kind: Resource, row?: Entry | Exit) {
  editing.value = kind
  clearError()
  if (kind === 'entries') {
    const entry = row as Entry | undefined
    Object.assign(entryForm, { id: undefined, name: '', subscription_id: '', mode: 'auto', node_key: null, enabled: true }, entry || {},
      { node_keys: [...(entry?.node_keys ?? (entry?.node_key ? [entry.node_key] : []))] })
  } else {
    const exit = row as Exit | undefined
    inputMode.value = 'manual'
    Object.assign(exitForm, {
      id: exit?.id, name: exit?.name || '', type: exit?.type || 'http', server: exit?.server || '',
      port: exit?.port || 80, username: exit?.username || '', password: '', tls: exit?.tls || false,
      content: '', enabled: exit?.enabled ?? true
    })
  }
  dialog.value = true
}
watch(() => entryForm.subscription_id, async id => {
  const request = ++nodeRequest
  nodes.value = []
  if (!id) { nodesLoading.value = false; return }
  nodesLoading.value = true
  try {
    const result: { nodes: SubNode[] } = await api('/subscriptions/' + id + '/nodes')
    if (request !== nodeRequest) return
    nodes.value = result.nodes.filter(n => n.included && n.type !== 'chain')
    const validKeys = entryForm.node_keys.filter(key => nodes.value.some(n => n.key === key))
    if (validKeys.length !== entryForm.node_keys.length) ElMessage.warning('部分已选节点在当前订阅中不可用，已移除，请检查选择')
    entryForm.node_keys = validKeys
  } catch (e: unknown) {
    if (request === nodeRequest) ElMessage.error(e instanceof Error ? e.message : '入口节点加载失败')
  } finally {
    if (request === nodeRequest) nodesLoading.value = false
  }
})
async function saveResource() {
  if (!canSave.value || busy.value) return
  await action(async () => {
    const kind = editing.value
    const id = activeForm.value.id
    let payload: Record<string, unknown>
    if (kind === 'entries') {
      const { id: _, node_key: _legacyKey, ...values } = entryForm
      payload = { ...values, node_keys: values.mode === 'fixed' ? values.node_keys : [] }
    } else {
      const { id: _, password, content, ...values } = exitForm
      payload = inputMode.value === 'yaml'
        ? { name: values.name, enabled: values.enabled, content }
        : { ...values, ...(!id || password ? { password } : {}) }
    }
    await api('/' + kind + (id ? '/' + id : ''), id ? 'PUT' : 'POST', payload)
    for (const key of Object.keys(probes)) if (!probes[key]?.loading) delete probes[key]
    dialog.value = false
    await reload()
    ElMessage.success('配置已保存')
  })
}
async function removeResource(kind: Resource, row: Entry | Exit) {
  await action(async () => {
    await ElMessageBox.confirm('删除「' + row.name + '」？被链路引用时无法删除。', '删除配置', { type: 'warning', confirmButtonText: '删除' })
    await api('/' + kind + '/' + row.id, 'DELETE')
    delete probes[kind + ':' + row.id]
    await reload()
    ElMessage.success('配置已删除')
  })
}
function clearSecrets() { exitForm.password = ''; exitForm.content = '' }
function addCurrent() { if (tab.value === 'chains') edit(); else editResource(tab.value) }
</script>

<template>
  <PageHeading title="链式" subtitle="把入口候选池、可选中转与出口代理串成链路，统一检测、监控与分发。">
    <el-button :icon="Refresh" :loading="busy" @click="action(reload)">刷新状态</el-button>
    <el-button v-if="tab === 'chains'" :icon="Collection" @click="showTemplates">链路模板</el-button>
    <el-button type="primary" :icon="Plus" @click="addCurrent">{{ tab === 'chains' ? '新建链路' : '添加' + labels[tab] }}</el-button>
  </PageHeading>

  <section class="panel chains-panel">
    <el-tabs v-model="tab" aria-label="链式配置">
      <el-tab-pane v-for="(label, kind) in labels" :key="kind" :name="kind">
        <template #label><span>{{ label }}</span><span class="tab-count" aria-hidden="true">{{ data[kind].length }}</span></template>
      </el-tab-pane>
    </el-tabs>

    <FilterBar>
      <el-input v-model="search" :prefix-icon="Search" clearable :placeholder="'搜索' + labels[tab] + '名称'" :aria-label="'搜索' + labels[tab]" />
      <el-radio-group v-if="tab === 'chains'" v-model="statusFilter" aria-label="按状态筛选" class="status-filter">
        <el-radio-button value="all">全部 {{ counts.all }}</el-radio-button>
        <el-radio-button value="active">正常 {{ counts.active }}</el-radio-button>
        <el-radio-button value="issue">异常 {{ counts.issue }}</el-radio-button>
        <el-radio-button value="off">已停用 {{ counts.off }}</el-radio-button>
      </el-radio-group>
    </FilterBar>

    <el-alert v-if="tab === 'chains' && !settings.mihomo" title="未检测到 Mihomo 内核：服务器中转与链路检测不可用，订阅原生分发不受影响。" type="warning" :closable="false" show-icon class="mb" />
    <el-skeleton v-if="initialLoading" :rows="5" animated />

    <!-- 链路 -->
    <template v-else-if="tab === 'chains'">
      <EmptyState v-if="!filteredChains.length" :title="data.chains.length ? '没有符合条件的链路' : '还没有链路'" :description="data.chains.length ? '试试其他名称或状态，或清空筛选条件。' : '链路依次经过入口、可选中转和出口。编辑器中可直接新建入口池和出口代理。'">
        <el-button v-if="data.chains.length" @click="search = ''; statusFilter = 'all'">清空筛选</el-button>
        <el-button v-else type="primary" :icon="Plus" @click="edit()">新建链路</el-button>
      </EmptyState>
      <template v-else>
        <div v-if="selected.length" class="selection-bar batch-bar" role="toolbar" aria-label="批量操作">
          <strong>已选 {{ selected.length }} 条</strong>
          <el-button v-for="(label, op) in batchOperations" :key="op" size="small" :disabled="busy" @click="batch(op)">{{ label }}</el-button>
          <el-button size="small" :disabled="busy" @click="openSubscription">加入订阅</el-button>
          <el-button size="small" type="danger" plain :disabled="busy" @click="batch('delete')">删除</el-button>
          <el-button size="small" text @click="selected = []">取消选择</el-button>
        </div>
        <div v-if="batchResults.length" class="batch-results" role="status">
          <div class="batch-results-head"><strong>批量操作结果</strong><el-button text size="small" @click="batchResults = []">关闭</el-button></div>
          <p v-for="item in batchResults" :key="item.id"><el-tag size="small" :type="item.ok ? 'success' : 'danger'">{{ item.ok ? (item.job_id ? '检测已排队' : '完成') : '失败' }}</el-tag> {{ item.name || item.id }} <span class="danger-text">{{ item.error }}</span><router-link v-if="item.job_id" to="/jobs">查看任务</router-link></p>
        </div>
        <div class="chain-list-head">
          <el-checkbox :model-value="allSelected" :indeterminate="someSelected" aria-label="全选链路" @change="selectAll(!!$event)" />
          <span>链路与路径</span><span class="head-metric">最近检测</span><span class="head-actions">操作</span>
        </div>
        <ul class="chain-list">
          <li v-for="row in filteredChains" :key="row.id" :class="['chain-card', 'is-' + chainState(row).tone, { 'is-selected': selected.includes(row.id) }]">
            <el-checkbox class="chain-check" :model-value="selected.includes(row.id)" :aria-label="'选择 ' + row.name" @change="select(row.id, !!$event)" />
            <div class="chain-main">
              <div class="chain-title">
                <span :class="['state-chip', 'is-' + chainState(row).tone]"><i aria-hidden="true" />{{ chainState(row).label }}</span>
                <h2><button type="button" class="chain-name" @click="openDrawer(row)">{{ row.name }}</button></h2>
              </div>
              <ChainPathPreview v-if="hopsOf(row).length" :hops="hopsOf(row)" />
              <p v-if="row.configuration_error" class="danger-text chain-error">{{ row.configuration_error }}</p>
              <div class="chain-meta">
                <span>{{ strategyLabel[row.paths?.[0]?.strategy || row.entry_mode] || '自动选择' }}</span>
                <span v-if="(row.paths?.length || 1) > 1">+{{ row.paths!.length - 1 }} 条备用路径</span>
                <span v-if="row.relay_enabled" class="chain-address"><span class="chain-address-label">中转</span><code>{{ relayAddress(row, settings) }}</code><el-button text size="small" :icon="CopyDocument" aria-label="复制连接信息" title="复制连接信息" @click="copyChainConnection(row)" /></span>
                <span v-else>仅订阅分发</span>
                <span v-if="row.relay_enabled && row.status.current_entry">当前入口 {{ row.status.current_entry }}</span>
                <span v-if="row.monitor_enabled">监控中</span>
              </div>
              <p v-if="row.enabled && row.status.error" class="danger-text chain-error">{{ row.status.error }}</p>
            </div>
            <div class="chain-metric" role="status" aria-live="polite">
              <span v-if="probes['chains:' + row.id]?.loading" class="muted">正在检测…</span>
              <template v-else-if="probeResult('chains', row)">
                <strong :class="probeResult('chains', row)!.reachable ? 'latency-good' : 'danger-text'">{{ probeResult('chains', row)!.reachable ? probeResult('chains', row)!.latency + ' ms' : '检测失败' }}</strong>
                <small v-if="probeResult('chains', row)!.entry" class="metric-line">经 {{ probeResult('chains', row)!.entry }}</small>
                <small v-if="probeResult('chains', row)!.error" class="metric-line danger-text" :title="probeResult('chains', row)!.error!">{{ probeResult('chains', row)!.error }}</small>
                <small class="muted">{{ time(probeResult('chains', row)!.tested_at) }}</small>
              </template>
              <span v-else class="muted">尚未检测</span>
            </div>
            <div class="chain-actions">
              <el-button :loading="probes['chains:' + row.id]?.loading" :disabled="!row.enabled" @click="probe('chains', row.id)">检测</el-button>
              <el-button :disabled="probes['chains:' + row.id]?.loading" @click="edit(row)">编辑</el-button>
              <ActionMenu :disabled="busy || probes['chains:' + row.id]?.loading" :items="chainActions(row)" @select="key => onChainAction(key, row)" />
            </div>
          </li>
        </ul>
      </template>
      <p class="note">服务器中转按链路策略自动选路，每条链路在订阅中显示为一个节点。对外域名需指向本服务器并映射中转端口。</p>
    </template>

    <!-- 入口 / 出口 -->
    <template v-else>
      <EmptyState v-if="!filteredResources.length" :title="search ? '没有符合条件的配置' : '还没有' + labels[tab]" :description="search ? '试试其他名称，或清空搜索条件。' : tab === 'entries' ? '入口池从订阅中划定候选节点，可在多条链路中复用。' : '出口是链路最后一跳的 HTTP、HTTPS 或 SOCKS5 代理。'">
        <el-button v-if="search" @click="search = ''">清空搜索</el-button>
        <el-button v-else type="primary" @click="editResource(tab as Resource)">添加{{ labels[tab] }}</el-button>
      </EmptyState>
      <div v-else class="table-region">
        <el-table :data="filteredResources" row-key="id">
          <el-table-column prop="name" label="名称" min-width="160" />
          <el-table-column :label="tab === 'entries' ? '来源与候选' : '地址与测速'" min-width="260">
            <template #default="{ row }">
              <template v-if="tab === 'entries'">{{ subscriptions.find(s => s.id === row.subscription_id)?.name || '来源不可用' }}<p class="muted">{{ row.mode === 'auto' ? '全部节点作为候选' : `已选 ${row.node_keys?.length || (row.node_key ? 1 : 0)} 个候选节点` }}</p></template>
              <template v-else><code>{{ row.server }}:{{ row.port }}</code> <el-tag size="small" type="info">{{ row.tls ? 'HTTPS' : row.type.toUpperCase() }}</el-tag><ProbeFeedback :loading="probes['exits:' + row.id]?.loading" :result="probeResult('exits', row)" /></template>
            </template>
          </el-table-column>
          <el-table-column label="被引用" width="100"><template #default="{ row }">{{ references(tab === 'entries' ? 'entry' : 'proxy', row.id) }} 条链路</template></el-table-column>
          <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
          <el-table-column label="操作" :width="tab === 'exits' ? 230 : 170">
            <template #default="{ row }"><div class="toolbar-actions config-row-actions"><el-button v-if="tab === 'exits'" text type="primary" :loading="probes['exits:' + row.id]?.loading" :disabled="!row.enabled" @click="probe('exits', row.id)">测速</el-button><el-button text :disabled="probes['exits:' + row.id]?.loading" @click="editResource(tab as Resource, row)">编辑</el-button><ActionMenu :disabled="busy || probes['exits:' + row.id]?.loading" :items="[{ key: 'remove', label: '删除配置', danger: true }]" @select="removeResource(tab as Resource, row)" /></div></template>
          </el-table-column>
        </el-table>
      </div>
      <p v-if="tab === 'exits'" class="note">出口测速从本服务器直连出口；需要经入口才能访问的出口，请检测对应链路。</p>
      <p v-else class="note">入口池只划定候选范围，使用方式（最快优先、指定节点等）在链路中设置。</p>
    </template>
  </section>

  <ChainDrawer ref="drawerRef" v-model="drawer" v-model:tab="drawerTab" :chain="drawerChain" @refresh="refresh" @edit="row => edit(row)" />

  <AppDialog v-model="dialog" :title="(activeForm.id ? '编辑' : '添加') + labels[editing]" @closed="clearSecrets">
    <el-form label-position="top" @submit.prevent="saveResource">
      <section class="form-section">
        <h3>基本信息</h3>
        <el-form-item label="名称"><el-input v-model="activeForm.name" @change="activeForm.name = $event" aria-label="配置名称" maxlength="80" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="activeForm.enabled" /></el-form-item>
      </section>
      <section v-if="editing === 'entries'" class="form-section">
        <h3>入口来源</h3>
        <el-alert v-if="!eligible.length" title="先创建包含账号或订阅源的订阅，再选择它作为入口。" type="info" :closable="false" class="mb" />
        <router-link v-if="!eligible.length" :to="{ name: 'subscriptions' }" class="text-link mb">前往订阅管理 →</router-link>
        <el-form-item label="入口来源订阅"><el-select v-model="entryForm.subscription_id" filterable class="full" aria-label="入口来源订阅"><el-option v-for="s in eligible" :key="s.id" :value="s.id" :label="s.name" /></el-select></el-form-item>
        <el-form-item label="候选范围"><el-radio-group v-model="entryForm.mode"><el-radio value="auto">全部节点</el-radio><el-radio value="fixed">选择候选节点（可多选）</el-radio></el-radio-group></el-form-item>
        <el-form-item v-if="entryForm.mode === 'fixed'" label="固定入口节点">
          <div class="full">
            <el-button :disabled="!entryForm.subscription_id || nodesLoading" @click="nodePickerVisible = true">选择节点</el-button>
            <p v-if="!entryForm.subscription_id" class="field-hint">请先选择入口来源订阅。</p>
            <p v-else class="field-hint" style="overflow-wrap: anywhere">已选 {{ entryForm.node_keys.length }} 个节点{{ selectedNodeNames ? '：' + selectedNodeNames : '，点击选择节点进行多选和测速。' }}</p>
          </div>
        </el-form-item>
        <p class="muted">这里只配置候选范围；链路中可选择“最快优先”“指定节点”等使用方式。节点列表中的延迟为节点自身检测结果。</p>
      </section>
      <section v-else class="form-section">
        <h3>出口配置</h3>
        <el-radio-group v-model="inputMode" class="mb" aria-label="出口输入方式"><el-radio-button value="manual">手动填写</el-radio-button><el-radio-button value="yaml">YAML 导入</el-radio-button></el-radio-group>
        <template v-if="inputMode === 'yaml'">
          <el-form-item label="出口 YAML"><el-input v-model="exitForm.content" @change="exitForm.content = $event" type="textarea" :rows="10" placeholder="支持单节点、单节点列表或 proxies 格式" aria-label="出口 YAML" /></el-form-item>
          <p class="muted">导入一个 HTTP、HTTPS 或 SOCKS5 出口节点。切换填写方式会保留本次草稿，只保存当前方式的配置。</p>
        </template>
        <template v-else>
          <el-form-item label="协议"><el-select v-model="exitForm.type" class="full"><el-option label="HTTP" value="http" /><el-option label="HTTPS" value="https" /><el-option label="SOCKS5" value="socks5" /></el-select></el-form-item>
          <div class="sub-grid">
            <!-- Also commit change-only input from IMEs and autofill before the controlled input restores its value. -->
            <el-form-item label="服务器域名或 IP"><el-input v-model="exitForm.server" @change="exitForm.server = $event" aria-label="出口服务器" autocapitalize="none" :spellcheck="false" /></el-form-item>
            <el-form-item label="端口"><el-input-number v-model="exitForm.port" :min="1" :max="65535" class="full" /></el-form-item>
          </div>
          <el-form-item label="用户名"><el-input v-model="exitForm.username" @change="exitForm.username = $event" autocomplete="off" /></el-form-item>
          <el-form-item label="密码（编辑时留空保留）"><el-input v-model="exitForm.password" @change="exitForm.password = $event" type="password" show-password autocomplete="new-password" /></el-form-item>
          <el-form-item label="启用 TLS"><el-switch v-model="exitForm.tls" /></el-form-item>
        </template>
      </section>
    </el-form>
    <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!canSave" @click="saveResource">保存配置</el-button></template>
  </AppDialog>

  <EntryNodeSelectDialog v-model="nodePickerVisible" :subscription-id="entryForm.subscription_id" :selected-keys="entryForm.node_keys" :nodes="nodes" @confirm="entryForm.node_keys = $event" @refreshed="nodes = $event" />

  <AppDialog v-model="templatesOpen" title="链路模板" :form="false">
    <EmptyState v-if="!templates.length" title="还没有模板" description="在链路编辑器中选择“保存为模板”，之后可以一键套用。" />
    <article v-for="t in templates" :key="t.id" class="template-row">
      <div class="grow"><strong>{{ t.name }}</strong><p v-if="t.error" class="danger-text">{{ t.error }}</p></div>
      <div class="toolbar-actions"><el-button type="primary" plain @click="router.push({ path: '/chains/new', query: { template: t.id } })">套用并编辑</el-button><el-button @click="router.push({ path: '/chains/new', query: { template: t.id, editTemplate: '1' } })">编辑模板</el-button><el-button type="danger" text @click="removeTemplate(t)">删除</el-button></div>
    </article>
    <template #footer><el-button @click="templatesOpen = false">关闭</el-button></template>
  </AppDialog>

  <AppDialog v-model="subscriptionDialog" title="加入订阅">
    <el-form label-position="top">
      <el-form-item label="目标订阅"><el-select v-model="subscriptionId" class="full" placeholder="新建订阅"><el-option value="" label="新建订阅" /><el-option v-for="s in subscriptions" :key="s.id" :value="s.id" :label="s.name" /></el-select></el-form-item>
      <el-form-item v-if="!subscriptionId" label="订阅名称"><el-input v-model="subscriptionName" /></el-form-item>
      <el-form-item label="完整配置中的链路方式">
        <el-select v-model="subscriptionMode"><el-option label="服务器中转（每条链路一个节点）" value="relay" /><el-option label="原生链路（客户端选择候选）" value="native" /></el-select>
        <p v-if="subscriptionMode === 'relay'" class="muted">链路需启用服务器中转，服务器会按各自的策略自动切换。</p>
      </el-form-item>
      <p class="muted">将 {{ selected.length }} 条链路加入订阅，已有普通节点及其他链路保留。</p>
    </el-form>
    <template #footer><el-button @click="subscriptionDialog = false">取消</el-button><el-button type="primary" :loading="busy" @click="saveSubscription">保存订阅</el-button></template>
  </AppDialog>
</template>

<style scoped>
.chains-panel { padding-top: 8px; }
.tab-count { margin-left: 6px; padding: 0 6px; border-radius: 999px; background: var(--surface-hover); color: var(--muted); font-size: 11px; line-height: 18px; font-variant-numeric: tabular-nums; }
.status-filter { flex-wrap: nowrap; }
.batch-bar { position: sticky; top: 0; z-index: 3; padding: 8px 12px; }
.batch-bar .el-button + .el-button { margin-left: 0; }
.batch-results { padding: 12px 14px; margin-bottom: 12px; background: var(--surface-soft); border: 1px solid var(--line); border-radius: 8px; overflow-wrap: anywhere; font-size: 13px; }
.batch-results-head { display: flex; justify-content: space-between; align-items: center; }
.batch-results p { margin: 6px 0 0; }
.batch-results a { margin-left: 10px; }
.chain-list-head, .chain-card { display: grid; grid-template-columns: 24px minmax(0, 1fr) 150px auto; align-items: center; gap: 16px; }
.chain-list-head { padding: 0 16px 8px; color: var(--muted); font-size: 12px; border-bottom: 1px solid var(--line); }
.head-actions { width: 196px; }
.chain-list { margin: 0; padding: 0; list-style: none; }
.chain-card { position: relative; padding: 16px; border-bottom: 1px solid var(--line); transition: background .15s; }
.chain-card::before { content: ""; position: absolute; left: 0; top: 16px; bottom: 16px; width: 3px; border-radius: 0 3px 3px 0; background: transparent; }
.chain-card.is-error::before { background: var(--danger); }
.chain-card.is-warn::before { background: var(--warning); }
.chain-card:hover, .chain-card.is-selected { background: var(--surface-soft); }
.chain-card.is-off .chain-main { opacity: .72; }
.chain-check { align-self: start; margin-top: 2px; height: 22px; }
.chain-main { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.chain-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.chain-title h2 { margin: 0; min-width: 0; font-size: 15px; }
.chain-name { max-width: 100%; padding: 0; border: 0; background: transparent; color: var(--ink); font: inherit; font-weight: 650; text-align: left; overflow-wrap: anywhere; }
.chain-name:hover { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.chain-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px; color: var(--muted); font-size: 12px; }
.chain-meta > span { display: inline-flex; align-items: center; gap: 4px; min-width: 0; overflow-wrap: anywhere; }
.chain-meta > span + span::before { content: ""; width: 3px; height: 3px; margin-right: 10px; border-radius: 50%; background: var(--subtle); flex: none; }
.chain-address-label { flex: none; white-space: nowrap; }
.chain-address code { padding: 1px 6px; border-radius: 4px; background: var(--code-bg); color: var(--ink); overflow-wrap: anywhere; }
.chain-address .el-button { min-height: 0; height: 22px; padding: 0 4px; }
.chain-error { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.chain-metric { display: flex; flex-direction: column; gap: 2px; min-width: 0; font-size: 12px; }
.chain-metric strong { font-size: 18px; font-weight: 650; font-variant-numeric: tabular-nums; }
.metric-line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chain-actions { display: flex; align-items: center; gap: 8px; width: 196px; justify-content: flex-end; }
.chain-actions .el-button + .el-button { margin-left: 0; }
.config-row-actions { flex-wrap: nowrap; white-space: nowrap; }
.config-row-actions > * { flex-shrink: 0; }
.template-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--line); }
.template-row p { margin: 4px 0 0; font-size: 12px; }
@media (max-width: 900px) {
  .chain-list-head { display: none; }
  .chain-card { grid-template-columns: 24px minmax(0, 1fr); gap: 12px; }
  .chain-metric { grid-column: 2; flex-direction: row; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
  .chain-metric strong { font-size: 14px; }
  .chain-actions { grid-column: 2; width: auto; justify-content: flex-start; }
  .status-filter { flex-wrap: wrap; }
}
@media (max-width: 767px) {
  .chain-card { padding: 14px 4px; }
  .chain-actions > .el-button { flex: 1; }
}
</style>
