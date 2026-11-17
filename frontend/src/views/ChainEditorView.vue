<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowLeft, ArrowUp, Close, CopyDocument, Plus, Rank } from '@element-plus/icons-vue'
import AppDialog from '../components/AppDialog.vue'
import ActionMenu from '../components/ActionMenu.vue'
import ChainNodeList from '../components/ChainNodeList.vue'
import ChainDiagnosticPanel from '../components/ChainDiagnosticPanel.vue'
import { api } from '../api'
import { load, settings } from '../store'
import { strategyLabel } from '../utils'
import type { ChainHop, ChainPath, ChainTemplate, Entry, Exit, SubNode, Subscription } from '../types'

const route = useRoute(), router = useRouter()
const id = computed(() => typeof route.params.id === 'string' ? route.params.id : '')
const entries = ref<Entry[]>([]), proxies = ref<Exit[]>([]), subscriptions = ref<Subscription[]>([]), templates = ref<ChainTemplate[]>([])
const nodes = reactive<Record<string, SubNode[]>>({}), busy = ref(false), loading = ref(true), error = ref(''), validated = ref(false), attempted = ref(false)
const pathIndex = ref(0), hopIndex = ref(0), picking = ref(false), dragIndex = ref<number | null>(null)
const diagnostic = ref(false), diagnosticDraft = ref<Record<string, any>>()
const blankHop = (source: ChainHop['source'] = 'proxy'): ChainHop => ({ source, ref: '', node_key: null })
const newPath = (): ChainPath => ({ name: '主路径', strategy: 'auto', tolerance_ms: 50, ordered_keys: [], hops: [blankHop('entry'), blankHop()] })
const form = reactive({ name: '', paths: [newPath()], lock_exit: true, interval: 300, monitor_enabled: true,
  relay_enabled: true, public_host: '', listen_port: undefined as number | undefined, enabled: true, revision: undefined as number | undefined })
const activePath = computed(() => form.paths[pathIndex.value]!)
const activeHop = computed(() => activePath.value.hops[hopIndex.value]!)
const sourceNodes = computed(() => nodes[activeHop.value.source + ':' + activeHop.value.ref] || [])
const isEntryHop = computed(() => activeHop.value.source === 'entry')
const strategies = [
  { value: 'auto', title: '最快优先', text: '定期测速完整路径，自动使用延迟最低的入口。' },
  { value: 'stable', title: '稳定优先', text: '新入口需快过容差才切换，减少频繁跳动。' },
  { value: 'fallback', title: '主备优先', text: '按设定顺序使用候选，前者不可用时依次切换。' },
  { value: 'fixed', title: '指定节点', text: '始终使用同一个入口节点，失效时不自动替换。' }
] as const
const sources = [{ value: 'entry', label: '入口候选池' }, { value: 'subscription', label: '订阅节点' }, { value: 'proxy', label: '已保存代理' }] as const
const sourceText: Record<ChainHop['source'], string> = { entry: '入口候选池', subscription: '订阅节点', proxy: '已保存代理' }

// ---- 草稿状态与离开保护 ----
const savedForm = ref('')
const dirty = computed(() => !loading.value && JSON.stringify(form) !== savedForm.value)
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save() }
}
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); window.addEventListener('keydown', onKeydown) })
onUnmounted(() => { window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('keydown', onKeydown) })
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try { await ElMessageBox.confirm('尚有未保存的链路配置，确定离开？', '离开编辑', { confirmButtonText: '放弃修改', cancelButtonText: '继续编辑', type: 'warning' }); return true }
  catch { return false }
})

// ---- 节点描述与校验 ----
function role(index: number, hops = activePath.value.hops) { return index === 0 ? '入口' : index === hops.length - 1 ? '出口' : '中转 ' + index }
function pathTitle(index: number) { return index === 0 ? '主路径' : '备用 ' + index }
function hopName(hop: ChainHop) {
  if (hop.source === 'entry') return entries.value.find(e => e.id === hop.ref)?.name || '选择入口池'
  if (hop.source === 'proxy') return proxies.value.find(e => e.id === hop.ref)?.name || '选择代理'
  if (!hop.ref) return '选择订阅节点'
  return nodes['subscription:' + hop.ref]?.find(n => n.key === hop.node_key)?.name || (hop.node_key ? '订阅节点（加载中或已失效）' : '选择节点')
}
function hopDetail(hop: ChainHop, index: number, path: ChainPath) {
  if (!hop.ref) return sourceText[hop.source]
  if (hop.source === 'proxy') {
    const proxy = proxies.value.find(e => e.id === hop.ref)
    return proxy ? (proxy.tls ? 'HTTPS' : proxy.type.toUpperCase()) + ' · ' + proxy.server + ':' + proxy.port : '代理已删除'
  }
  if (hop.source === 'subscription') return '订阅 · ' + (subscriptions.value.find(s => s.id === hop.ref)?.name || '来源不可用')
  const count = nodes['entry:' + hop.ref]?.length
  const fixed = index === 0 && path.strategy === 'fixed' ? ' · ' + (nodes['entry:' + hop.ref]?.find(n => n.key === hop.node_key)?.name || '未指定节点') : ''
  return (count == null ? '' : count + ' 个候选 · ') + strategyLabel[path.strategy] + fixed
}
function hopIssue(hop: ChainHop, index: number, path: ChainPath) {
  if (!hop.ref) return hop.source === 'entry' ? '未选择入口池' : hop.source === 'proxy' ? '未选择代理' : '未选择来源订阅'
  if (hop.source === 'subscription' && !hop.node_key) return '未选择订阅节点'
  if (index === 0 && hop.source === 'entry' && path.strategy === 'fixed' && !hop.node_key) return '未指定入口节点'
  if (index === 0 && hop.source === 'entry' && path.strategy === 'fallback' && !path.ordered_keys.length) return '未设置主备候选顺序'
  return ''
}
const issues = computed(() => {
  const list: { text: string, path: number, hop: number }[] = []
  if (!form.name.trim()) list.push({ text: '请填写链路名称', path: -1, hop: -1 })
  form.paths.forEach((path, p) => path.hops.forEach((hop, h) => {
    const issue = hopIssue(hop, h, path)
    if (issue) list.push({ text: `${pathTitle(p)} · ${role(h, path.hops)}：${issue}`, path: p, hop: h })
  }))
  if (form.relay_enabled && form.listen_port != null && (form.listen_port < settings.proxy_port_start || form.listen_port > settings.proxy_port_end)) {
    list.push({ text: `监听端口需在 ${settings.proxy_port_start}–${settings.proxy_port_end} 范围内`, path: -1, hop: -1 })
  }
  return list
})
const status = computed(() => attempted.value && issues.value.length ? { tone: 'warn', text: `待完善 ${issues.value.length} 项` }
  : validated.value ? { tone: 'ok', text: '配置已校验' } : dirty.value ? { tone: 'idle', text: '有未保存修改' } : { tone: 'off', text: id.value ? '与已保存一致' : '新建草稿' })
function focusIssue() {
  const first = issues.value[0]
  if (first && first.path >= 0) { pathIndex.value = first.path; hopIndex.value = first.hop }
}
function ready() {
  attempted.value = true
  if (!issues.value.length) return true
  focusIssue()
  return false
}

// ---- 路径编辑 ----
function payload() { return { ...JSON.parse(JSON.stringify(form)), listen_port: form.listen_port ?? null } }
function nodeRestriction(node: SubNode) { return hopIndex.value > 0 && node.type === 'hysteria2' ? 'Hysteria2 只能作为第一跳' : null }
async function fetchNodes(hop: ChainHop) {
  if (!hop.ref || hop.source === 'proxy') return
  const key = hop.source + ':' + hop.ref
  const response = await api(hop.source === 'entry' ? '/entries/' + hop.ref + '/nodes' : '/subscriptions/' + hop.ref + '/nodes')
  nodes[key] = (Array.isArray(response) ? response : response.nodes).filter((n: SubNode) => n.type !== 'chain')
}
function setSource(source: ChainHop['source']) {
  Object.assign(activeHop.value, blankHop(source))
  if (hopIndex.value === 0) { activePath.value.strategy = source === 'entry' ? 'auto' : 'fixed'; activePath.value.ordered_keys = [] }
}
function setRef(value: string) {
  activeHop.value.ref = value; activeHop.value.node_key = null
  if (hopIndex.value === 0) activePath.value.ordered_keys = []
}
function setStrategy(value: ChainPath['strategy']) {
  activePath.value.strategy = value
  if (value !== 'fixed') activeHop.value.node_key = null
}
const selectedKeys = computed({
  get: () => activePath.value.strategy === 'fallback' && isEntryHop.value ? activePath.value.ordered_keys : activeHop.value.node_key ? [activeHop.value.node_key] : [],
  set: keys => {
    if (activePath.value.strategy === 'fallback' && isEntryHop.value) activePath.value.ordered_keys = keys
    else activeHop.value.node_key = keys[0] || null
  }
})
const needsNode = computed(() => activeHop.value.source === 'subscription' || (isEntryHop.value && ['fixed', 'fallback'].includes(activePath.value.strategy)))
function insert(index: number) {
  if (activePath.value.hops.length >= 5) return
  activePath.value.hops.splice(index, 0, blankHop()); hopIndex.value = index
}
function removeHop(index: number) {
  activePath.value.hops.splice(index, 1); hopIndex.value = Math.min(index, activePath.value.hops.length - 1)
}
function moveHop(from: number, to: number) {
  const hops = activePath.value.hops
  if (from <= 0 || from >= hops.length - 1 || to <= 0 || to >= hops.length - 1) return
  hops.splice(to, 0, hops.splice(from, 1)[0]!); hopIndex.value = to
}
function drop(index: number) { if (dragIndex.value != null) moveHop(dragIndex.value, index); dragIndex.value = null }
function addPath() {
  if (form.paths.length >= 3) return
  const path = JSON.parse(JSON.stringify(activePath.value)) as ChainPath
  path.name = '备用路径 ' + form.paths.length; form.paths.push(path); pathIndex.value = form.paths.length - 1; hopIndex.value = 0
}
function movePath(direction: number) {
  const index = pathIndex.value, target = index + direction
  if (target < 0 || target >= form.paths.length) return
  form.paths.splice(target, 0, form.paths.splice(index, 1)[0]!); pathIndex.value = target
}
async function removePath() {
  try { await ElMessageBox.confirm(`移除「${activePath.value.name}」？`, '移除路径', { type: 'warning', confirmButtonText: '移除' }) } catch { return }
  form.paths.splice(pathIndex.value, 1); pathIndex.value = 0; hopIndex.value = 0
}
function selectPath(index: number) { pathIndex.value = index; hopIndex.value = 0 }
const hopPanel = ref<HTMLElement>()
/** 窄屏时配置面板位于路径下方，选中节点后滚动过去，避免点击无反馈。 */
async function editHop(path: number, hop: number) {
  pathIndex.value = path; hopIndex.value = hop
  if (!matchMedia('(max-width: 1100px)').matches) return
  await nextTick(); hopPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function orderCandidate(index: number, direction: number) {
  const values = activePath.value.ordered_keys, target = index + direction
  if (target < 0 || target >= values.length) return
  values.splice(target, 0, values.splice(index, 1)[0]!)
}

// ---- 校验、保存与检测 ----
async function check() {
  if (!ready()) return
  error.value = ''; busy.value = true
  try { await api('/chains/validate', 'POST', payload()); validated.value = true; ElMessage.success('路径配置有效') }
  catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function save() {
  if (busy.value || !ready()) return
  busy.value = true; error.value = ''
  try {
    await api('/chains' + (id.value ? '/' + id.value : ''), id.value ? 'PUT' : 'POST', payload())
    savedForm.value = JSON.stringify(form)
    await load(); ElMessage.success(form.enabled ? '链路已保存并启用' : '链路已保存，当前停用'); await router.push('/chains')
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
function probe() {
  if (!ready()) return
  diagnosticDraft.value = payload(); diagnostic.value = true
}

// ---- 模板与内联资源 ----
const templateDialog = ref(false), templateId = ref('')
async function saveTemplate() {
  try {
    const response = await ElMessageBox.prompt('模板仅保存节点引用和策略，不保存端口、密码或检测历史。', '保存模板', { inputValue: form.name || '链路模板', inputValidator: v => !!v?.trim() || '请输入名称' })
    const editing = route.query.editTemplate === '1' && templateId.value
    await api('/chain-templates' + (editing ? '/' + templateId.value : ''), editing ? 'PUT' : 'POST', { name: response.value, config: payload() })
    templates.value = await api('/chain-templates'); ElMessage.success('模板已保存')
  } catch (e) { if (e !== 'cancel' && e !== 'close') error.value = (e as Error).message }
}
async function useTemplate(value: string) {
  const template = templates.value.find(t => t.id === value)
  if (!template) return
  templateId.value = value
  Object.assign(form, JSON.parse(JSON.stringify(template.config)), { revision: form.revision, listen_port: form.listen_port, name: template.name })
  pathIndex.value = 0; hopIndex.value = 0; templateDialog.value = false
  error.value = template.error ? '模板引用已失效，请重新选择节点：' + template.error : ''
  await warmNodes()
}
function onMenu(key: string) {
  if (key === 'template') templateDialog.value = true
  if (key === 'save-template') saveTemplate()
}
const resourceDialog = ref(false), resourceKind = ref<'entry' | 'proxy'>('proxy')
const resource = reactive({ name: '', subscription_id: '', type: 'http', server: '', port: 80, username: '', password: '' })
function openResource(kind: 'entry' | 'proxy') {
  resourceKind.value = kind; Object.assign(resource, { name: '', subscription_id: '', type: 'http', server: '', port: 80, username: '', password: '' }); resourceDialog.value = true
}
async function createResource() {
  busy.value = true
  try {
    const kind = resourceKind.value
    const values = kind === 'entry' ? { name: resource.name, subscription_id: resource.subscription_id, mode: 'auto' } : { name: resource.name, type: resource.type, server: resource.server, port: resource.port, username: resource.username, password: resource.password }
    const created = await api(kind === 'entry' ? '/entries' : '/exits', 'POST', values)
    if (kind === 'entry') entries.value = await api('/entries'); else proxies.value = await api('/exits')
    Object.assign(activeHop.value, { source: kind, ref: created.id, node_key: null })
    if (hopIndex.value === 0) activePath.value.strategy = kind === 'entry' ? 'auto' : 'fixed'
    resourceDialog.value = false; resource.password = ''; ElMessage.success('资源已保存并选中')
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function warmNodes() {
  const hops = form.paths.flatMap(p => p.hops), unique = [...new Map(hops.map(h => [h.source + ':' + h.ref, h])).values()]
  await Promise.all(unique.map(h => fetchNodes(h).catch(() => {})))
}
watch(() => [activeHop.value?.source, activeHop.value?.ref], async () => {
  const hop = activeHop.value
  if (!hop) return
  picking.value = true
  try { await fetchNodes(hop) } catch (e) { error.value = (e as Error).message } finally { picking.value = false }
})
watch(() => JSON.stringify(form), () => { validated.value = false })
watch(() => [form.lock_exit, JSON.stringify(activePath.value.hops.at(-1))], () => {
  if (!form.lock_exit) return
  const last = activePath.value.hops.at(-1)!
  for (const path of form.paths) if (JSON.stringify(path.hops.at(-1)) !== JSON.stringify(last)) path.hops[path.hops.length - 1] = { ...last }
})
onMounted(async () => {
  try {
    const [e, p, s, t] = await Promise.all([api('/entries'), api('/exits'), api('/subscriptions'), api('/chain-templates')])
    entries.value = e; proxies.value = p; subscriptions.value = s.filter((v: Subscription) => v.account_ids.length || v.source_ids.length); templates.value = t
    if (id.value) {
      const row = await api('/chains/' + id.value)
      for (const key of Object.keys(form)) if (row[key] !== undefined && row[key] !== null) (form as any)[key] = row[key]
    } else if (typeof route.query.template === 'string') {
      await useTemplate(route.query.template)
    }
    await warmNodes()
  } catch (e) { error.value = (e as Error).message } finally { savedForm.value = JSON.stringify(form); loading.value = false }
})
</script>

<template>
  <div class="editor-bar">
    <el-button text :icon="ArrowLeft" class="editor-back" @click="router.push('/chains')">返回链路</el-button>
    <div class="editor-title">
      <h1>{{ id ? '编辑链路' : '新建链路' }}</h1>
      <span :class="['state-chip', 'is-' + status.tone]" role="status"><i aria-hidden="true" />{{ status.text }}</span>
    </div>
    <div class="editor-actions">
      <ActionMenu label="模板操作" :disabled="loading" :items="[{ key: 'template', label: '套用模板', disabled: !templates.length }, { key: 'save-template', label: '保存为模板' }]" @select="onMenu" />
      <el-button :disabled="busy || loading" @click="probe">检测草稿</el-button>
      <el-button :loading="busy" :disabled="loading" @click="check">校验配置</el-button>
      <el-button type="primary" :loading="busy" :disabled="loading" title="Ctrl + S" @click="save">保存链路</el-button>
    </div>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mb" />
  <div v-if="attempted && issues.length" class="editor-issues" role="alert">
    <strong>保存前还需完善：</strong>
    <ul><li v-for="(issue, index) in issues" :key="index"><button v-if="issue.path >= 0" type="button" class="text-link" @click="editHop(issue.path, issue.hop)">{{ issue.text }}</button><span v-else>{{ issue.text }}</span></li></ul>
  </div>
  <el-skeleton v-if="loading" :rows="8" animated />
  <div v-else class="editor-grid">
    <div class="editor-main">
      <section class="panel editor-basics">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="链路名称" :error="attempted && !form.name.trim() ? '请填写链路名称' : ''"><el-input v-model="form.name" maxlength="80" aria-label="链路名称" placeholder="例如：美国固定出口" /></el-form-item>
          <el-form-item label="链路状态"><el-switch v-model="form.enabled" active-text="保存后启用" inactive-text="停用" aria-label="保存后启用" /></el-form-item>
        </el-form>
      </section>

      <section class="panel">
        <div class="section-head">
          <div><h2>路径编排</h2><p class="muted">流量依次经过以下节点，每条路径 2–5 跳；最多 3 条主备路径。</p></div>
        </div>
        <div class="path-tabs" role="tablist" aria-label="主备路径">
          <button v-for="(path, index) in form.paths" :key="index" type="button" role="tab" :aria-selected="index === pathIndex" @click="selectPath(index)"><span>{{ pathTitle(index) }}</span><small>{{ path.name }} · {{ path.hops.length }} 跳</small></button>
          <el-button :icon="CopyDocument" :disabled="form.paths.length >= 3" @click="addPath">复制为备用路径</el-button>
        </div>
        <div class="path-options">
          <el-input v-model="activePath.name" maxlength="80" aria-label="路径名称" />
          <template v-if="form.paths.length > 1">
            <el-button :icon="ArrowUp" :disabled="pathIndex === 0" @click="movePath(-1)">提高优先级</el-button>
            <el-button :icon="ArrowDown" :disabled="pathIndex === form.paths.length - 1" @click="movePath(1)">降低优先级</el-button>
            <el-button type="danger" plain @click="removePath">移除此路径</el-button>
          </template>
        </div>
        <el-switch v-if="form.paths.length > 1" v-model="form.lock_exit" class="lock-exit" active-text="所有路径锁定同一出口（切换后出口 IP 不变）" />

        <ol class="hop-flow" aria-label="可视化路径编辑器">
          <template v-for="(hop, index) in activePath.hops" :key="index">
            <li :class="['hop-card', { selected: index === hopIndex, invalid: attempted && hopIssue(hop, index, activePath), middle: index > 0 && index < activePath.hops.length - 1 }]" :draggable="index > 0 && index < activePath.hops.length - 1" @dragstart="dragIndex = index" @dragover.prevent @drop.prevent="drop(index)">
              <span class="hop-index" aria-hidden="true">{{ index + 1 }}</span>
              <button type="button" class="hop-select" :aria-pressed="index === hopIndex" @click="editHop(pathIndex, index)">
                <small>{{ role(index) }} · {{ sourceText[hop.source] }}</small>
                <strong>{{ hopName(hop) }}</strong>
                <span>{{ attempted && hopIssue(hop, index, activePath) || hopDetail(hop, index, activePath) }}</span>
              </button>
              <div v-if="index > 0 && index < activePath.hops.length - 1" class="hop-tools">
                <el-icon class="drag-handle" aria-hidden="true"><Rank /></el-icon>
                <el-button text size="small" :icon="ArrowUp" :disabled="index === 1" :aria-label="'前移第 ' + (index + 1) + ' 跳'" @click="moveHop(index, index - 1)" />
                <el-button text size="small" :icon="ArrowDown" :disabled="index === activePath.hops.length - 2" :aria-label="'后移第 ' + (index + 1) + ' 跳'" @click="moveHop(index, index + 1)" />
                <el-button text size="small" :icon="Close" :aria-label="'移除第 ' + (index + 1) + ' 跳'" @click="removeHop(index)" />
              </div>
            </li>
            <li v-if="index < activePath.hops.length - 1" class="hop-link">
              <button type="button" class="insert-hop" :disabled="activePath.hops.length >= 5" :aria-label="'在第 ' + (index + 1) + ' 跳后添加节点'" :title="activePath.hops.length >= 5 ? '每条路径最多 5 跳' : '插入中转节点'" @click="insert(index + 1)"><el-icon><Plus /></el-icon><span>插入中转</span></button>
            </li>
          </template>
        </ol>
      </section>

      <section class="panel">
        <div class="section-head"><div><h2>运行与监控</h2><p class="muted">服务器中转提供带认证的代理端口；原生订阅在客户端独立选路，不受此处影响。</p></div></div>
        <el-form label-position="top" class="run-grid" @submit.prevent>
          <el-form-item label="服务器中转"><el-switch v-model="form.relay_enabled" active-text="开启带认证的中转监听" /></el-form-item>
          <el-form-item label="自动监控"><el-switch v-model="form.monitor_enabled" active-text="记录延迟与出口 IP" /></el-form-item>
          <template v-if="form.relay_enabled">
            <el-form-item label="监听端口"><el-input-number v-model="form.listen_port" :min="settings.proxy_port_start" :max="settings.proxy_port_end" :controls="false" :placeholder="id ? '留空保留当前端口' : '留空自动分配'" aria-label="监听端口" class="full" /><span class="field-hint">范围 {{ settings.proxy_port_start }}–{{ settings.proxy_port_end }}</span></el-form-item>
            <el-form-item label="对外域名或 IP"><el-input v-model="form.public_host" placeholder="留空使用系统代理地址" autocapitalize="none" :spellcheck="false" /></el-form-item>
          </template>
          <el-form-item v-if="form.monitor_enabled" label="检测间隔（秒）"><el-input-number v-model="form.interval" :min="60" :max="3600" :step="60" class="full" /><span class="field-hint">历史保留 30 天；IP 查询失败不会直接判定链路不可用。</span></el-form-item>
        </el-form>
      </section>
    </div>

    <aside ref="hopPanel" class="panel hop-panel" :aria-label="'第 ' + (hopIndex + 1) + ' 跳配置'">
      <div class="section-head"><div><span class="eyebrow">{{ pathTitle(pathIndex) }} · 第 {{ hopIndex + 1 }} 跳</span><h2>{{ role(hopIndex) }}节点</h2></div></div>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="节点来源">
          <el-radio-group :model-value="activeHop.source" aria-label="节点来源" class="source-switch" @change="(value: unknown) => setSource(value as ChainHop['source'])">
            <el-radio-button v-for="s in sources" :key="s.value" :value="s.value" :disabled="s.value === 'entry' && hopIndex > 0">{{ s.label }}</el-radio-button>
          </el-radio-group>
          <span v-if="hopIndex > 0" class="field-hint">入口候选池只能作为第一跳。</span>
        </el-form-item>
        <el-form-item :label="isEntryHop ? '入口池' : activeHop.source === 'proxy' ? '代理' : '来源订阅'">
          <div class="resource-row">
            <el-select :model-value="activeHop.ref" filterable placeholder="请选择" aria-label="选择节点资源" @change="setRef">
              <template v-if="isEntryHop"><el-option v-for="e in entries" :key="e.id" :value="e.id" :label="e.name + (e.enabled ? '' : '（停用）')" :disabled="!e.enabled" /></template>
              <template v-else-if="activeHop.source === 'proxy'"><el-option v-for="e in proxies" :key="e.id" :value="e.id" :label="e.name + ' · ' + e.type + (e.enabled ? '' : '（停用）')" :disabled="!e.enabled" /></template>
              <template v-else><el-option v-for="s in subscriptions" :key="s.id" :value="s.id" :label="s.name" :disabled="!s.enabled" /></template>
            </el-select>
            <el-button v-if="isEntryHop" :icon="Plus" @click="openResource('entry')">新建入口池</el-button>
            <el-button v-else-if="activeHop.source === 'proxy'" :icon="Plus" @click="openResource('proxy')">添加代理</el-button>
          </div>
        </el-form-item>

        <el-form-item v-if="isEntryHop" label="入口选路策略">
          <div class="strategy-grid" role="radiogroup" aria-label="入口选路策略">
            <button v-for="s in strategies" :key="s.value" type="button" role="radio" :aria-checked="activePath.strategy === s.value" :class="['strategy-card', { selected: activePath.strategy === s.value }]" @click="setStrategy(s.value)"><strong>{{ s.title }}</strong><span>{{ s.text }}</span></button>
          </div>
        </el-form-item>
        <el-form-item v-if="isEntryHop && activePath.strategy === 'stable'" label="切换容差（ms）"><el-input-number v-model="activePath.tolerance_ms" :min="0" :max="5000" :step="10" /></el-form-item>

        <template v-if="needsNode && activeHop.ref">
          <el-form-item :label="activePath.strategy === 'fallback' && isEntryHop ? '主备候选（按点击顺序排列优先级）' : isEntryHop ? '指定入口节点' : '订阅节点'">
            <ChainNodeList v-model="selectedKeys" class="full" :nodes="sourceNodes" :loading="picking" :multiple="activePath.strategy === 'fallback' && isEntryHop" :label="activePath.strategy === 'fallback' && isEntryHop ? '主备候选顺序' : '固定节点'" :disabled="nodeRestriction" />
          </el-form-item>
          <ol v-if="activePath.strategy === 'fallback' && isEntryHop && activePath.ordered_keys.length" class="candidate-order" aria-label="候选优先级">
            <li v-for="(key, index) in activePath.ordered_keys" :key="key">
              <span class="order-badge">{{ index + 1 }}</span><span class="grow">{{ sourceNodes.find(n => n.key === key)?.name || '节点已失效' }}</span>
              <el-button text size="small" :icon="ArrowUp" :disabled="index === 0" aria-label="上移" @click="orderCandidate(index, -1)" />
              <el-button text size="small" :icon="ArrowDown" :disabled="index === activePath.ordered_keys.length - 1" aria-label="下移" @click="orderCandidate(index, 1)" />
              <el-button text size="small" :icon="Close" aria-label="移除候选" @click="activePath.ordered_keys.splice(index, 1)" />
            </li>
          </ol>
        </template>
        <p v-else-if="isEntryHop && activeHop.ref" class="hint-box">从入口池 {{ sourceNodes.length }} 个候选中，按经过全部跳的完整路径测速结果自动选择。</p>
        <p class="field-hint">列表延迟来自节点自身检测；链路选路以经过全部跳的结果为准。失效的固定节点不会按名称自动替换。</p>
      </el-form>
    </aside>
  </div>

  <AppDialog v-model="resourceDialog" :title="resourceKind === 'entry' ? '新建入口池' : '添加代理'" @closed="resource.password = ''">
    <el-form label-position="top" @submit.prevent="createResource">
      <el-form-item label="资源名称"><el-input v-model="resource.name" aria-label="资源名称" /></el-form-item>
      <el-form-item v-if="resourceKind === 'entry'" label="来源订阅"><el-select v-model="resource.subscription_id" class="full" aria-label="资源来源订阅"><el-option v-for="s in subscriptions" :key="s.id" :value="s.id" :label="s.name" :disabled="!s.enabled" /></el-select></el-form-item>
      <template v-else>
        <el-form-item label="协议"><el-select v-model="resource.type" class="full"><el-option value="http" label="HTTP" /><el-option value="https" label="HTTPS" /><el-option value="socks5" label="SOCKS5" /></el-select></el-form-item>
        <div class="sub-grid">
          <el-form-item label="服务器"><el-input v-model="resource.server" aria-label="代理服务器" autocapitalize="none" :spellcheck="false" /></el-form-item>
          <el-form-item label="端口"><el-input-number v-model="resource.port" :min="1" :max="65535" class="full" aria-label="代理端口" /></el-form-item>
        </div>
        <el-form-item label="用户名"><el-input v-model="resource.username" autocomplete="off" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="resource.password" type="password" show-password autocomplete="new-password" /></el-form-item>
      </template>
      <p class="muted">资源会独立保存，可在其他链路复用；取消链路编辑不会删除已保存资源。</p>
    </el-form>
    <template #footer><el-button @click="resourceDialog = false">取消</el-button><el-button type="primary" :loading="busy" @click="createResource">保存并选中</el-button></template>
  </AppDialog>

  <AppDialog v-model="templateDialog" title="套用模板" :form="false">
    <p class="muted">套用后会替换当前路径与运行设置，名称改为模板名称；端口与配置版本保持不变。</p>
    <article v-for="t in templates" :key="t.id" class="template-row">
      <div class="grow"><strong>{{ t.name }}</strong><p v-if="t.error" class="danger-text">引用需修复：{{ t.error }}</p></div>
      <el-button type="primary" plain @click="useTemplate(t.id)">套用</el-button>
    </article>
    <template #footer><el-button @click="templateDialog = false">关闭</el-button></template>
  </AppDialog>

  <AppDialog v-model="diagnostic" title="草稿检测" size="760px" :form="false">
    <ChainDiagnosticPanel :draft="diagnosticDraft" :active="diagnostic" />
    <template #footer><el-button @click="diagnostic = false">关闭</el-button></template>
  </AppDialog>
</template>

<style scoped>
.editor-bar { position: sticky; top: -30px; z-index: 5; display: flex; align-items: center; gap: 12px 16px; flex-wrap: wrap; margin: -30px -32px 20px; padding: 18px 32px 14px; background: color-mix(in srgb, var(--page) 88%, transparent); backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); }
.editor-back { margin-left: -8px; }
.editor-title { display: flex; align-items: center; gap: 12px; flex: 1; min-width: 0; }
.editor-title h1 { margin: 0; font-size: 22px; white-space: nowrap; }
.editor-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.editor-actions .el-button + .el-button { margin-left: 0; }
.editor-issues { padding: 12px 16px; margin-bottom: 16px; border: 1px solid var(--warning); border-radius: 10px; background: var(--warning-soft); color: var(--warning); font-size: 13px; }
.editor-issues ul { margin: 6px 0 0; padding-left: 20px; }
.editor-issues .text-link { color: inherit; text-decoration: underline; font-size: 13px; }
.editor-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(340px, 1fr); gap: 20px; align-items: start; }
.editor-main { min-width: 0; }
.editor-main > .panel:last-child { margin-bottom: 0; }
.editor-basics .el-form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 24px; }
.editor-basics .el-form-item { margin-bottom: 0; }
.section-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
.section-head h2 { margin-bottom: 4px; }
.section-head p { margin: 0; font-size: 12px; }
.section-head .eyebrow { margin-bottom: 6px; letter-spacing: 1px; }
.path-tabs { display: flex; flex-wrap: wrap; align-items: stretch; gap: 8px; margin-bottom: 12px; }
.path-tabs > button[role=tab] { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; min-width: 0; max-width: 220px; padding: 8px 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); text-align: left; }
.path-tabs > button[role=tab] small { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 11px; }
.path-tabs > button[aria-selected=true] { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
.path-tabs > .el-button { height: auto; min-height: 48px; border-style: dashed; }
.path-options { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; }
.path-options > .el-input { flex: 0 1 240px; }
.path-options .el-button + .el-button { margin-left: 0; }
.lock-exit { margin-bottom: 8px; height: auto; white-space: normal; }
.hop-flow { margin: 8px 0 0; padding: 0; list-style: none; }
.hop-card { position: relative; display: flex; align-items: center; gap: 12px; padding: 6px 8px 6px 6px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); transition: border-color .15s, box-shadow .15s; }
.hop-card:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
.hop-card.selected { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.hop-card.invalid { border-color: var(--warning); background: var(--warning-soft); }
.hop-card.middle { cursor: grab; }
.hop-index { display: grid; place-items: center; flex: none; width: 32px; height: 32px; margin-left: 6px; border-radius: 50%; background: var(--surface-hover); color: var(--muted); font-weight: 650; font-size: 13px; }
.hop-card:first-child .hop-index { background: var(--accent-soft); color: var(--accent); }
.hop-card:last-child .hop-index { background: var(--success-soft); color: var(--success); }
.hop-select { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; padding: 8px 4px; border: 0; background: transparent; color: var(--ink); text-align: left; }
.hop-select small { color: var(--muted); font-size: 12px; }
.hop-select strong { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.hop-select span { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.hop-card.invalid .hop-select span { color: var(--warning); }
.hop-tools { display: flex; align-items: center; gap: 0; flex: none; }
.hop-tools .el-button + .el-button { margin-left: 0; }
.drag-handle { color: var(--subtle); margin-right: 4px; }
.hop-link { position: relative; display: flex; align-items: center; height: 36px; padding-left: 29px; }
.hop-link::before { content: ""; position: absolute; left: 30px; top: 0; bottom: 0; border-left: 2px dashed var(--line); }
.insert-hop { position: relative; display: inline-flex; align-items: center; gap: 6px; margin-left: -11px; padding: 2px 10px 2px 2px; border: 0; border-radius: 999px; background: transparent; color: var(--muted); font-size: 12px; opacity: .75; }
.insert-hop .el-icon { display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; border: 1px solid var(--line); background: var(--surface); color: var(--accent); }
.insert-hop:hover:not(:disabled), .insert-hop:focus-visible { opacity: 1; color: var(--accent); background: var(--accent-soft); }
.insert-hop:disabled { cursor: not-allowed; opacity: .35; }
.run-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; }
.hop-panel { position: sticky; top: 76px; margin-bottom: 0; max-height: calc(100dvh - 160px); overflow-y: auto; overscroll-behavior: contain; }
.source-switch { display: flex; width: 100%; }
.source-switch :deep(.el-radio-button) { flex: 1; }
.source-switch :deep(.el-radio-button__inner) { width: 100%; padding-left: 8px; padding-right: 8px; }
.resource-row { display: flex; gap: 8px; width: 100%; }
.resource-row .el-select { flex: 1; min-width: 0; }
.strategy-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; width: 100%; }
.strategy-card { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); color: var(--ink); text-align: left; }
.strategy-card strong { font-size: 13px; }
.strategy-card span { color: var(--muted); font-size: 12px; line-height: 1.5; }
.strategy-card:hover { background: var(--surface-soft); }
.strategy-card.selected { border-color: var(--accent); background: var(--accent-soft); box-shadow: inset 0 0 0 1px var(--accent); }
.strategy-card.selected strong { color: var(--accent); }
.candidate-order { margin: -6px 0 16px; padding: 0; list-style: none; }
.candidate-order li { display: flex; align-items: center; gap: 8px; padding: 6px 4px; border-bottom: 1px solid var(--line); font-size: 13px; overflow-wrap: anywhere; }
.candidate-order .el-button + .el-button { margin-left: 0; }
.order-badge { display: grid; place-items: center; flex: none; width: 20px; height: 20px; border-radius: 50%; background: var(--accent); color: #fff; font-size: 11px; font-weight: 650; }
html.dark .order-badge { color: #12213c; }
.hint-box { padding: 12px; margin: 0 0 12px; border-radius: 8px; background: var(--surface-soft); color: var(--muted); font-size: 12px; line-height: 1.7; }
.template-row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 14px 0; border-bottom: 1px solid var(--line); }
.template-row p { margin: 4px 0 0; font-size: 12px; }
@media (max-width: 1100px) {
  .editor-grid { grid-template-columns: 1fr; }
  .hop-panel { position: static; max-height: none; overflow: visible; scroll-margin-top: 150px; }
}
@media (max-width: 1023px) { .editor-bar { top: -24px; margin: -24px -22px 20px; padding: 16px 22px 12px; } }
@media (max-width: 767px) {
  .editor-bar { position: static; margin: 0 0 16px; padding: 0 0 12px; background: transparent; backdrop-filter: none; }
  .editor-actions { width: 100%; }
  .editor-actions > * { flex: 1 1 auto; }
  .editor-basics .el-form, .run-grid { grid-template-columns: 1fr; }
  .editor-basics .el-form-item + .el-form-item { margin-top: 16px; }
  .strategy-grid { grid-template-columns: 1fr; }
  .path-tabs > button[role=tab] { flex: 1 1 120px; }
  .path-options > .el-input { flex-basis: 100%; }
  .source-switch :deep(.el-radio-button__inner) { font-size: 12px; padding-left: 4px; padding-right: 4px; }
}
</style>
