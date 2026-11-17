<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowRight, Connection, Plus, Refresh, Search } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import PageHeading from '../components/PageHeading.vue'
import AppDialog from '../components/AppDialog.vue'
import EntryNodeSelectDialog from '../components/EntryNodeSelectDialog.vue'
import ActionMenu from '../components/ActionMenu.vue'
import EmptyState from '../components/EmptyState.vue'
import FilterBar from '../components/FilterBar.vue'
import ProbeFeedback from '../components/ProbeFeedback.vue'
import { api } from '../api'
import { action, busy, chains, load, subscriptions, settings, clearError } from '../store'
import { copyText } from '../utils'
import type { Chain, ConnectionInfo, Entry, Exit, MenuAction, SubNode, ProbeResult } from '../types'

type Kind = 'entries' | 'exits' | 'chains'
type EntryForm = Omit<Entry, 'id'> & { id?: string }
type ExitForm = Omit<Exit, 'id' | 'has_password'> & { id?: string, password: string, content: string }
type ChainForm = Omit<Chain, 'id' | 'status' | 'listen_port' | 'selected_entry' | 'probe'> & { id?: string, listen_port: number | undefined }

const router = useRouter()
const tab = ref<Kind>('chains')
const data = reactive<{ entries: Entry[], exits: Exit[], chains: Chain[] }>({ entries: [], exits: [], chains: [] })
const labels: Record<Kind, string> = { chains: '链路', entries: '入口', exits: '出口' }
const statusLabels: Record<string, string> = { running: '监听运行中', pending: '等待启动', error: '启动失败', disabled: '中转关闭' }
const initialLoading = ref(true)
const probes = reactive<Record<string, { loading: boolean, result?: ProbeResult }>>({})
function probeResult(kind: 'chains' | 'exits', row: Chain | Exit) {
  const local = probes[kind + ':' + row.id]?.result
  return local && (!row.probe || local.tested_at > row.probe.tested_at) ? local : row.probe
}
const search = ref('')
const filtered = computed(() => data[tab.value].filter(row => row.name.toLowerCase().includes(search.value.toLowerCase())))
const filteredChains = computed(() => data.chains.filter(row => row.name.toLowerCase().includes(search.value.toLowerCase())))
const dialog = ref(false)
const editing = ref<Kind>('chains')
const entryForm = reactive<EntryForm>({ name: '', subscription_id: '', mode: 'auto', node_key: null, node_keys: [], enabled: true })
const exitForm = reactive<ExitForm>({ name: '', type: 'http', server: '', port: 80, username: '', password: '', tls: false, content: '', enabled: true })
const chainForm = reactive<ChainForm>({ name: '', entry_id: '', exit_id: '', entry_mode: 'auto', entry_node_key: null, enabled: true, relay_enabled: true, listen_port: undefined, public_host: '' })
const chainNodes = ref<SubNode[]>([])
const chainNodesLoading = ref(false)
const chainNodesError = ref('')
let chainNodeRequest = 0
const activeForm = computed(() => editing.value === 'entries' ? entryForm : editing.value === 'exits' ? exitForm : chainForm)
const inputMode = ref<'manual' | 'yaml'>('manual')
const nodes = ref<SubNode[]>([])
const nodesLoading = ref(false)
const nodePickerVisible = ref(false)
const selectedNodeNames = computed(() => nodes.value.filter(n => entryForm.node_keys.includes(n.key)).map(n => n.name).join('、'))
watch(dialog, open => { if (!open) nodePickerVisible.value = false })
const connection = ref<ConnectionInfo | null>(null)
const connectionVisible = ref(false)
const passwordVisible = ref(false)
const manualCopy = ref('')
const eligible = computed(() => subscriptions.value.filter(s => s.account_ids.length || s.source_ids.length))
let nodeRequest = 0

const portError = computed(() => {
  const port = chainForm.listen_port
  if (port == null) return ''
  if (!Number.isInteger(port) || port < settings.proxy_port_start || port > settings.proxy_port_end) {
    return `请填写 ${settings.proxy_port_start}–${settings.proxy_port_end} 范围内的整数端口`
  }
  const owner = data.chains.find(row => row.id !== chainForm.id && row.listen_port === port)
  return owner ? `端口 ${port} 已被链路「${owner.name}」使用，请更换端口` : ''
})

const canSave = computed(() => {
  if (!activeForm.value.name.trim()) return false
  if (editing.value === 'entries') return !!entryForm.subscription_id && (entryForm.mode === 'auto' || (!nodesLoading.value && entryForm.node_keys.length > 0 && entryForm.node_keys.every(key => nodes.value.some(n => n.key === key))))
  if (editing.value === 'exits') return inputMode.value === 'yaml' ? !!exitForm.content.trim() : !!exitForm.server.trim() && !!exitForm.port
  return !!chainForm.entry_id && !!chainForm.exit_id && !portError.value &&
    (chainForm.entry_mode === 'auto' || (!chainNodesLoading.value && chainNodes.value.some(n => n.key === chainForm.entry_node_key)))
})
const chainActions: MenuAction[] = [
  { key: 'generate', label: '生成订阅' },
  { key: 'rotate', label: '轮换认证', divided: true },
  { key: 'remove', label: '删除链路', danger: true }
]

async function refresh() {
  const [entries, exits, chainList] = await Promise.all([api('/entries'), api('/exits'), api('/chains')])
  Object.assign(data, { entries, exits, chains: chainList })
}
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
    for (const row of data.chains) {
      row.status.current_entry = null
      row.status.latency = null
    }
  } finally {
    if (!disposed) statusTimer = setTimeout(pollStatus, 5000)
  }
}
onMounted(async () => {
  await action(refresh)
  initialLoading.value = false
  if (!disposed) statusTimer = setTimeout(pollStatus, 5000)
})
onUnmounted(() => { disposed = true; clearTimeout(statusTimer); ++chainNodeRequest })
watch(chains, value => { data.chains = value })
watch(tab, () => { search.value = '' })

function edit(kind: Kind, row?: Entry | Exit | Chain) {
  editing.value = kind
  clearError()
  if (kind === 'entries') {
    const entry = row as Entry | undefined
    Object.assign(entryForm, { id: undefined, name: '', subscription_id: '', mode: 'auto', node_key: null, enabled: true }, entry || {},
      { node_keys: [...(entry?.node_keys ?? (entry?.node_key ? [entry.node_key] : []))] })
  } else if (kind === 'exits') {
    const exit = row as Exit | undefined
    inputMode.value = 'manual'
    Object.assign(exitForm, {
      id: exit?.id, name: exit?.name || '', type: exit?.type || 'http', server: exit?.server || '',
      port: exit?.port || 80, username: exit?.username || '', password: '', tls: exit?.tls || false,
      content: '', enabled: exit?.enabled ?? true
    })
  } else {
    const chain = row as Chain | undefined
    Object.assign(chainForm, {
      id: chain?.id, name: chain?.name || '', entry_id: chain?.entry_id || '', exit_id: chain?.exit_id || '',
      entry_mode: chain?.entry_mode || 'auto', entry_node_key: chain?.entry_node_key || null,
      enabled: chain?.enabled ?? true, relay_enabled: chain?.relay_enabled ?? true,
      listen_port: chain?.listen_port, public_host: chain?.public_host || ''
    })
  }
  dialog.value = true
}

watch([dialog, editing, () => chainForm.entry_id], async ([open, kind, id]) => {
  const request = ++chainNodeRequest
  chainNodes.value = []
  chainNodesError.value = ''
  chainNodesLoading.value = false
  if (!open || kind !== 'chains' || !id) return
  chainNodesLoading.value = true
  try {
    const result: SubNode[] = await api('/entries/' + id + '/nodes')
    if (request !== chainNodeRequest) return
    chainNodes.value = result
    if (chainForm.entry_node_key && !result.some(n => n.key === chainForm.entry_node_key)) {
      chainNodesError.value = '原指定入口已不在候选中，请重新选择，或切换为自动测速选择。'
    }
  } catch (e) {
    if (request === chainNodeRequest) chainNodesError.value = e instanceof Error ? e.message : '入口节点加载失败'
  } finally {
    if (request === chainNodeRequest) chainNodesLoading.value = false
  }
})

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

async function save() {
  if (!canSave.value || busy.value) return
  await action(async () => {
    const kind = editing.value
    const id = activeForm.value.id
    let payload: Record<string, unknown>
    if (kind === 'entries') {
      const { id: _, node_key: _legacyKey, ...values } = entryForm
      payload = { ...values, node_keys: values.mode === 'fixed' ? values.node_keys : [] }
    } else if (kind === 'exits') {
      const { id: _, password, content, ...values } = exitForm
      payload = inputMode.value === 'yaml'
        ? { name: values.name, enabled: values.enabled, content }
        : { ...values, ...(!id || password ? { password } : {}) }
    } else {
      const { id: _, ...values } = chainForm
      payload = { ...values, entry_node_key: values.entry_mode === 'fixed' ? values.entry_node_key : null, listen_port: values.listen_port ?? null }
    }
    await api('/' + kind + (id ? '/' + id : ''), id ? 'PUT' : 'POST', payload)
    for (const key of Object.keys(probes)) if (!probes[key]?.loading) delete probes[key]
    dialog.value = false
    await Promise.all([refresh(), load()])
    ElMessage.success('配置已保存')
  })
}
async function remove(kind: Kind, row: Entry | Exit | Chain) {
  await action(async () => {
    await ElMessageBox.confirm('删除「' + row.name + '」？', '删除配置', { type: 'warning' })
    await api('/' + kind + '/' + row.id, 'DELETE')
    delete probes[kind + ':' + row.id]
    await Promise.all([refresh(), load()])
    ElMessage.success('配置已删除')
  })
}
async function showConnection(row: Chain, rotate = false) {
  await action(async () => {
    if (rotate) await ElMessageBox.confirm('轮换后原中转密码立即失效，客户端需要更新。', '轮换认证', { type: 'warning' })
    connection.value = await api('/chains/' + row.id + '/' + (rotate ? 'rotate' : 'connection'), rotate ? 'POST' : 'GET')
    passwordVisible.value = false
    manualCopy.value = ''
    connectionVisible.value = true
    await refresh()
  })
}
async function probe(kind: 'chains' | 'exits', id: string) {
  const key = kind + ':' + id
  if (probes[key]?.loading) return
  probes[key] = { loading: true }
  try {
    probes[key].result = await api('/' + kind + '/' + id + '/probe', 'POST')
    if (kind === 'chains') {
      await refresh().catch(() => ElMessage.warning('检测已完成，当前入口状态刷新失败，请稍后刷新状态'))
    }
  } catch (e) {
    probes[key].result = { reachable: false, latency: null, error: e instanceof Error ? e.message : '检测失败，请重试', tested_at: Date.now() / 1000 }
  } finally { probes[key].loading = false }
}
async function generate(row: Chain) {
  await action(async () => {
    await api('/subscriptions', 'POST', { name: row.name + ' 订阅', chain_ids: [row.id] })
    await load()
    router.push('/subscriptions')
  })
}
function onChainAction(key: string, row: Chain) {
  if (key === 'generate') return generate(row)
  if (key === 'rotate') return showConnection(row, true)
  if (key === 'remove') return remove('chains', row)
}
function relayAddress(row: Chain) {
  let host = row.public_host || settings.proxy_public_host
  if (!host) {
    try { host = new URL(settings.public_base_url).hostname } catch { host = '系统域名' }
  }
  if (host.includes(':') && !host.startsWith('[')) host = '[' + host + ']'
  return host + ':' + row.listen_port
}
function formatConnection(c: ConnectionInfo) {
  const host = c.host.includes(':') && !c.host.startsWith('[') ? '[' + c.host + ']' : c.host
  const address = host + ':' + c.port
  const auth = encodeURIComponent(c.username) + ':' + encodeURIComponent(c.password)
  const maskedAuth = encodeURIComponent(c.username) + ':••••••••'
  return [
    { label: '地址', value: address, masked: address },
    { label: '账号', value: c.username, masked: c.username },
    { label: '密码', value: c.password, masked: '••••••••' },
    { label: 'HTTP URI', value: 'http://' + auth + '@' + address, masked: 'http://' + maskedAuth + '@' + address },
    { label: 'SOCKS5 URI', value: 'socks5://' + auth + '@' + address, masked: 'socks5://' + maskedAuth + '@' + address }
  ]
}
const connectionFields = computed(() => connection.value ? formatConnection(connection.value) : [])
async function copyConnection(value: string) {
  if (!await copyText(value)) {
    manualCopy.value = value
    ElMessage.info('无法自动复制，请在下方选择内容并手动复制')
  }
}
function clearConnection() { connection.value = null; manualCopy.value = ''; passwordVisible.value = false }
function clearSecrets() { exitForm.password = ''; exitForm.content = '' }
</script>

<template>
  <PageHeading title="链式" subtitle="从订阅选择入口，连接出口代理，组合成可复用的链路。">
    <el-button :icon="Refresh" :loading="busy" @click="action(refresh)">刷新状态</el-button>
    <el-button type="primary" :icon="Plus" :disabled="tab === 'chains' && (!data.entries.length || !data.exits.length)" @click="edit(tab)">添加{{ labels[tab] }}</el-button>
  </PageHeading>
  <section class="panel">
    <el-tabs v-model="tab" aria-label="代理配置">
      <el-tab-pane v-for="(label, kind) in labels" :key="kind" :label="label" :name="kind" />
    </el-tabs>
    <FilterBar>
      <el-input v-model="search" :prefix-icon="Search" clearable :placeholder="'搜索' + labels[tab] + '名称'" :aria-label="'搜索' + labels[tab]" />
      <template #actions><span class="muted">{{ filtered.length }} 个{{ labels[tab] }}</span></template>
    </FilterBar>
    <el-skeleton v-if="initialLoading" :rows="5" animated />
    <EmptyState v-else-if="!filtered.length" :title="search ? '没有符合条件的配置' : '还没有' + labels[tab]" :description="search ? '试试其他名称，或清空搜索条件。' : tab === 'chains' ? '先创建入口和出口，再将它们连接为一条代理链路。' : '添加后即可在多条链路中重复使用。'">
      <el-button v-if="search" @click="search = ''">清空搜索</el-button>
      <template v-else-if="tab === 'chains' && (!data.entries.length || !data.exits.length)">
        <el-button v-if="!data.entries.length" @click="tab = 'entries'; edit('entries')">配置入口</el-button>
        <el-button v-if="!data.exits.length" @click="tab = 'exits'; edit('exits')">配置出口</el-button>
      </template>
      <el-button v-else type="primary" @click="edit(tab)">添加{{ labels[tab] }}</el-button>
    </EmptyState>
    <div v-else-if="tab === 'chains'" class="account-grid">
      <article v-for="row in filteredChains" :key="row.id" class="panel account-card chain-card">
        <div class="card-top">
          <div class="account-avatar"><el-icon><Connection /></el-icon></div>
          <div class="grow"><h2>{{ row.name }}</h2><p class="muted">{{ row.relay_enabled ? '服务器中转 · 端口 ' + row.listen_port : '客户端原生链路' }}</p></div>
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
        </div>
        <div class="chain-path" aria-label="代理路径">
          <div class="chain-endpoint"><span>入口</span><strong>{{ data.entries.find(e => e.id === row.entry_id)?.name || '入口不可用' }}</strong></div>
          <el-icon><ArrowRight /></el-icon>
          <div class="chain-endpoint"><span>出口</span><strong>{{ data.exits.find(e => e.id === row.exit_id)?.name || '出口不可用' }}</strong></div>
        </div>
        <div class="chain-selection">
          <el-tag size="small" :type="row.entry_mode === 'fixed' ? 'warning' : 'primary'">{{ row.entry_mode === 'fixed' ? '指定入口' : '自动测速选择' }}</el-tag>
          <p v-if="row.entry_mode === 'fixed'">指定节点：<strong>{{ row.selected_entry || '节点已失效，请重新选择' }}</strong></p>
          <p v-if="row.relay_enabled">中转当前入口：<strong>{{ row.status.current_entry || (row.status.state === 'running' ? '暂未获取，等待状态刷新' : '中转未运行') }}</strong></p>
          <p v-else class="muted">{{ row.entry_mode === 'fixed' ? '客户端更新订阅后使用指定入口。' : '当前入口由客户端测速选择，请在客户端查看。' }}</p>
          <small v-if="row.relay_enabled" class="muted">客户端原生订阅独立选路；此处显示服务器中转入口。</small>
        </div>
        <div class="chain-status">
          <el-tag :type="row.status.state === 'error' ? 'danger' : row.status.state === 'running' ? 'success' : 'info'">{{ statusLabels[row.status.state] || row.status.state }}</el-tag>
          <span v-if="row.status.latency != null">{{ row.status.latency }} ms</span>
          <div v-if="row.relay_enabled" class="chain-address"><code>{{ relayAddress(row) }}</code></div>
          <div v-if="row.status.error" class="danger-text">{{ row.status.error }}</div>
          <ProbeFeedback :loading="probes['chains:' + row.id]?.loading" :result="probeResult('chains', row)" :entry-label="row.entry_mode === 'fixed' ? '本次检测指定入口' : '检测时最快入口'" />
        </div>
        <div class="card-actions">
          <el-button type="primary" plain :disabled="busy" @click="showConnection(row)">连接信息</el-button>
          <el-button :loading="probes['chains:' + row.id]?.loading" :disabled="!row.enabled" @click="probe('chains', row.id)">检测链路</el-button>
          <el-button :disabled="probes['chains:' + row.id]?.loading" @click="edit('chains', row)">编辑</el-button>
          <ActionMenu :disabled="busy || probes['chains:' + row.id]?.loading" :items="chainActions" @select="key => onChainAction(key, row)" />
        </div>
      </article>
    </div>
    <el-table v-else :data="filtered" row-key="id">
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column label="配置" min-width="240">
        <template #default="{ row }">
          <template v-if="tab === 'entries'">{{ subscriptions.find(s => s.id === row.subscription_id)?.name || '来源不可用' }}<p class="muted">{{ row.mode === 'auto' ? '全部节点作为候选' : `已选 ${row.node_keys?.length || (row.node_key ? 1 : 0)} 个候选节点` }} · 使用方式由链路决定</p></template>
          <template v-else><code>{{ row.server }}:{{ row.port }}</code><p class="muted">{{ row.tls ? 'HTTPS' : row.type.toUpperCase() }}</p><ProbeFeedback :loading="probes['exits:' + row.id]?.loading" :result="probeResult('exits', row)" /></template>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" :width="tab === 'exits' ? 300 : 220">
        <template #default="{ row }"><div class="toolbar-actions config-row-actions"><el-button v-if="tab === 'exits'" text type="primary" :loading="probes['exits:' + row.id]?.loading" :disabled="!row.enabled" @click="probe('exits', row.id)">测速</el-button><el-button text :disabled="probes['exits:' + row.id]?.loading" @click="edit(tab, row)">编辑</el-button><ActionMenu :disabled="busy || probes['exits:' + row.id]?.loading" :items="[{ key: 'remove', label: '删除配置', danger: true }]" @select="remove(tab, row)" /></div></template>
      </el-table-column>
    </el-table>
    <p v-if="tab === 'exits'" class="note">出口测速从本服务器直连出口，检测代理可用性和延迟；需要经入口才能访问的出口，请检测对应链路。</p>
    <p v-if="tab === 'chains'" class="note">完整订阅支持原生两跳，节点订阅使用服务器中转。可在订阅管理中将链路合并到已有订阅。域名需指向本服务器并映射代理端口。</p>
    <el-alert v-if="tab === 'chains' && !settings.mihomo" title="当前未检测到 Mihomo，服务器中转需要安装内核。" type="warning" :closable="false" show-icon class="mt" />
  </section>

  <AppDialog v-model="dialog" :title="(activeForm.id ? '编辑' : '添加') + labels[editing]" @closed="clearSecrets">
    <el-form label-position="top" @submit.prevent="save">
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
        <p class="muted">这里配置入口候选范围。创建或编辑链路时，可选择“自动测速选择”或“指定入口”。自动模式每 5 分钟检测经过出口的完整链路并按延迟择优，非下载带宽测速；节点列表中的延迟为节点自身检测结果。</p>
      </section>
      <section v-else-if="editing === 'exits'" class="form-section">
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
      <template v-else>
        <section class="form-section">
          <h3>连接入口与出口</h3>
          <el-form-item label="入口"><el-select v-model="chainForm.entry_id" filterable class="full" aria-label="链路入口" @change="chainForm.entry_node_key = null"><el-option v-for="e in data.entries" :key="e.id" :label="e.name + (e.enabled ? '' : '（已停用）')" :value="e.id" /></el-select></el-form-item>
          <el-form-item label="入口使用方式"><el-radio-group v-model="chainForm.entry_mode" aria-label="入口使用方式"><el-radio value="auto">自动测速选择</el-radio><el-radio value="fixed">指定入口</el-radio></el-radio-group></el-form-item>
          <template v-if="chainForm.entry_mode === 'fixed'">
            <el-form-item label="指定入口节点">
              <el-select v-model="chainForm.entry_node_key" filterable :loading="chainNodesLoading" :disabled="!chainForm.entry_id || chainNodesLoading" placeholder="选择一个入口节点" no-data-text="当前入口没有可用候选节点" class="full" aria-label="指定入口节点" @change="chainNodesError = ''">
                <el-option v-for="node in chainNodes" :key="node.key" :value="node.key" :label="node.name + ' · ' + node.server + ':' + node.port" />
              </el-select>
            </el-form-item>
            <p v-if="chainNodesError" class="danger-text" role="alert">{{ chainNodesError }}</p>
            <p class="muted">始终使用指定节点；节点失效时链路不可用，不会自动改用其他入口。</p>
          </template>
          <p v-else class="muted">从入口候选中选择完整链路延迟最低的可用节点，每 5 分钟自动检测。保存后点击“检测链路”可立即测速并更新中转入口。</p>
          <el-form-item label="出口"><el-select v-model="chainForm.exit_id" filterable class="full" aria-label="链路出口"><el-option v-for="e in data.exits" :key="e.id" :label="e.name + (e.enabled ? '' : '（已停用）')" :value="e.id" /></el-select></el-form-item>
        </section>
        <section class="form-section">
          <h3>服务器中转</h3>
          <el-form-item label="启用服务器中转"><el-switch v-model="chainForm.relay_enabled" /></el-form-item>
          <template v-if="chainForm.relay_enabled">
            <el-form-item label="监听端口" :error="portError">
              <el-input-number v-model="chainForm.listen_port" :min="settings.proxy_port_start" :max="settings.proxy_port_end" :step="1" :controls="false" :placeholder="chainForm.id ? '留空保留当前端口' : '留空自动分配'" aria-label="监听端口" aria-describedby="proxy-port-hint" class="full" />
              <p id="proxy-port-hint" class="muted">允许范围：{{ settings.proxy_port_start }}–{{ settings.proxy_port_end }}。{{ chainForm.id ? '留空保留当前端口。' : '留空由系统分配未使用的端口。' }}</p>
            </el-form-item>
            <el-form-item label="对外域名或 IP（留空使用系统代理地址）"><el-input v-model="chainForm.public_host" @change="chainForm.public_host = $event" autocapitalize="none" :spellcheck="false" /></el-form-item>
            <p class="muted">保存后在“连接信息”获取域名:端口及认证信息，HTTP 与 SOCKS5 使用同一端口。</p>
          </template>
          <p v-else class="muted">仍可在完整订阅中使用客户端原生链路。直接代理访问和仅节点订阅需要启用服务器中转。</p>
        </section>
      </template>
    </el-form>
    <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!canSave" @click="save">保存配置</el-button></template>
  </AppDialog>

  <EntryNodeSelectDialog v-model="nodePickerVisible" :subscription-id="entryForm.subscription_id" :selected-keys="entryForm.node_keys" :nodes="nodes" @confirm="entryForm.node_keys = $event" @refreshed="nodes = $event" />

  <AppDialog v-model="connectionVisible" title="代理连接信息" :form="false" @closed="clearConnection">
    <p class="muted">使用中转地址连接代理。复制操作会包含完整认证信息。</p>
    <el-button @click="passwordVisible = !passwordVisible">{{ passwordVisible ? '隐藏认证信息' : '显示认证信息' }}</el-button>
    <div v-for="field in connectionFields" :key="field.label" class="connection-field">
      <div><span class="field-label">{{ field.label }}</span><el-button text type="primary" :aria-label="'复制' + field.label" @click="copyConnection(field.value)">复制</el-button></div>
      <code>{{ passwordVisible ? field.value : field.masked }}</code>
    </div>
    <div v-if="manualCopy" class="manual-copy"><el-form-item label="手动复制内容"><el-input :model-value="manualCopy" type="textarea" :rows="6" readonly aria-label="手动复制内容" /></el-form-item></div>
    <template #footer><el-button type="primary" @click="copyConnection(connectionFields.map(f => f.label + '：' + f.value).join('\n'))">复制连接信息</el-button></template>
  </AppDialog>
</template>

<style scoped>
.config-row-actions { flex-wrap: nowrap; white-space: nowrap; }
.config-row-actions > * { flex-shrink: 0; }
.chain-address { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
.chain-address code { min-width: 0; overflow-wrap: anywhere; }
.chain-selection { padding: 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-soft); overflow-wrap: anywhere; }
.chain-selection p { margin: 8px 0; }
</style>
