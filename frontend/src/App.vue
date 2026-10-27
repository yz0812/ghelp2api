<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Grid, Key, Link, Tickets, Setting, Plus, Refresh, Search, Download, SwitchButton, ArrowRight } from '@element-plus/icons-vue'
import { api, setCsrf } from './api'
import type { Account, Node, Subscription, Job } from './types'

const loggedIn = ref(false), booting = ref(true), loading = ref(false), busy = ref(false)
const active = ref('overview'), error = ref('')
const credentials = reactive({ username: 'admin', password: '' })
const accounts = ref<Account[]>([]), nodes = ref<Node[]>([]), subscriptions = ref<Subscription[]>([]), jobs = ref<Job[]>([])
const settings = reactive({ refresh_minutes: 30, public_base_url: '', extension_version: '' })
const search = ref(''), accountFilter = ref(''), regionFilter = ref(''), statusFilter = ref('')
const selectedNodes = ref<Node[]>([])
const importVisible = ref(false), importText = ref(''), accountVisible = ref(false)
const editingAccount = reactive({ id: '', name: '', token: '', enabled: true })
const subVisible = ref(false), subForm = reactive({ id: '', name: '', account_ids: [] as string[], regions: [] as string[], exclude_failed: false, enabled: true })
const previewVisible = ref(false), preview = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
const nav = [
  { id: 'overview', label: '连接总览', icon: Grid }, { id: 'accounts', label: '账号管理', icon: Key },
  { id: 'nodes', label: '节点列表', icon: Connection }, { id: 'subscriptions', label: '订阅管理', icon: Link },
  { id: 'jobs', label: '任务记录', icon: Tickets }, { id: 'settings', label: '系统设置', icon: Setting }
]
const current = computed(() => nav.find(n => n.id === active.value)!)
const healthy = computed(() => accounts.value.filter(a => a.enabled && a.status === 'ready').length)
const usable = computed(() => nodes.value.filter(n => n.enabled && !n.stale))
const regions = computed(() => [...new Set(nodes.value.map(n => n.region))].sort())
const filteredNodes = computed(() => nodes.value.filter(n =>
  (!accountFilter.value || n.account_id === accountFilter.value) &&
  (!regionFilter.value || n.region === regionFilter.value) &&
  (!search.value || `${n.server} ${n.account_name} ${n.region}`.toLowerCase().includes(search.value.toLowerCase())) &&
  (!statusFilter.value || (statusFilter.value === 'ok' ? n.reachable === true : statusFilter.value === 'failed' ? n.reachable === false : n.reachable === null))))
const running = computed(() => jobs.value.filter(j => ['pending', 'running'].includes(j.status)))
const stateLabel: Record<string, string> = { pending: '等待获取', ready: '已就绪', error: '获取失败', invalid: '凭证失效', running: '进行中', completed: '已完成', partial: '部分失败', interrupted: '已中断' }
function time(value: number | null) { return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—' }
function bytes(value: unknown) {
  if (typeof value !== 'number') return '上游未提供'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']; let n = value, i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i ? 2 : 0)} ${units[i]}`
}
async function action(fn: () => Promise<void>) {
  busy.value = true; error.value = ''
  try { await fn() } catch (e: any) { if (e !== 'cancel' && e !== 'close') { error.value = e.message || '操作失败'; ElMessage.error(error.value) } }
  finally { busy.value = false }
}
async function load() {
  loading.value = true
  try {
    const [a, n, s, j, config] = await Promise.all([api('/accounts'), api('/nodes'), api('/subscriptions'), api('/jobs'), api('/settings')])
    accounts.value = a; nodes.value = n; subscriptions.value = s; jobs.value = j; Object.assign(settings, config)
  } finally { loading.value = false }
}
function schedulePoll() {
  clearTimeout(timer)
  if (!loggedIn.value) return
  timer = setTimeout(async () => {
    try { await load() }
    catch (e: any) { error.value = e.message }
    finally { schedulePoll() }
  }, running.value.length ? 2000 : 15000)
}
async function saveSettings() { await action(async () => { await api('/settings', 'PUT', { refresh_minutes: settings.refresh_minutes }); ElMessage.success('设置已保存') }) }
async function login() { await action(async () => { const r = await api('/auth/login', 'POST', credentials); setCsrf(r.csrf); credentials.password = ''; loggedIn.value = true; await load(); schedulePoll() }) }
async function logout() { await action(async () => { await api('/auth/logout', 'POST'); expire() }) }
function expire() { loggedIn.value = false; accounts.value = []; nodes.value = []; subscriptions.value = []; clearTimeout(timer); setCsrf('') }
async function refresh(id = 'all') { await action(async () => { await api(`/accounts/${id}/refresh`, 'POST'); await load(); schedulePoll(); ElMessage.success('刷新任务已提交') }) }
async function importAccounts() {
  await action(async () => {
    const text = importText.value.trim(); let entries: any[]
    if (text.startsWith('{') || text.startsWith('[')) { const obj = JSON.parse(text); entries = Array.isArray(obj) ? obj : obj.accounts }
    else entries = text.split(/\r?\n/).filter(Boolean).map((line, i) => { const parts = line.trim().split(/[,，\s]+/); return { name: parts.length > 1 ? parts.slice(0, -1).join(' ') : `账号 ${accounts.value.length + i + 1}`, token: parts.at(-1) } })
    if (!Array.isArray(entries) || !entries.length) throw new Error('请填写 token 或有效的导入 JSON')
    const result = await api('/accounts/import', 'POST', { accounts: entries })
    importText.value = ''; importVisible.value = false; await load()
    ElMessage.success(`已添加 ${result.results.filter((x: any) => x.status === 'created').length} 个账号，重复项已跳过`)
  })
}
async function readImport(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) { if (file.size > 1000000) { ElMessage.error('文件不能超过 1 MB'); return }; importText.value = await file.text() }
  input.value = ''
}
function editAccount(a: Account) { Object.assign(editingAccount, { id: a.id, name: a.name, token: '', enabled: a.enabled }); accountVisible.value = true }
async function saveAccount() { await action(async () => { await api(`/accounts/${editingAccount.id}`, 'PATCH', { name: editingAccount.name, enabled: editingAccount.enabled, ...(editingAccount.token ? { token: editingAccount.token } : {}) }); editingAccount.token = ''; accountVisible.value = false; await load() }) }
async function removeAccount(a: Account) { await action(async () => { await ElMessageBox.confirm(`删除「${a.name}」及其节点？订阅将移除此账号。`, '删除账号', { type: 'warning' }); await api(`/accounts/${a.id}`, 'DELETE'); await load() }) }
function newSub(account?: Account) { Object.assign(subForm, { id: '', name: account ? `${account.name} 订阅` : '我的聚合订阅', account_ids: account ? [account.id] : accounts.value.filter(a => a.enabled).map(a => a.id), regions: [], exclude_failed: false, enabled: true }); subVisible.value = true }
function editSub(s: Subscription) { Object.assign(subForm, { ...s, account_ids: [...s.account_ids], regions: [...s.regions] }); subVisible.value = true }
async function saveSub() { await action(async () => { const { id, name, account_ids, regions, exclude_failed, enabled } = subForm; await api(id ? `/subscriptions/${id}` : '/subscriptions', id ? 'PUT' : 'POST', { name, account_ids, regions, exclude_failed, enabled }); subVisible.value = false; await load(); ElMessage.success('订阅已保存') }) }
async function rotate(s: Subscription) { await action(async () => { await ElMessageBox.confirm('轮换后旧订阅地址立即失效，客户端需要重新添加地址。', '轮换订阅密钥', { type: 'warning' }); await api(`/subscriptions/${s.id}/rotate`, 'POST'); await load() }) }
async function removeSub(s: Subscription) { await action(async () => { await ElMessageBox.confirm(`删除「${s.name}」？原订阅地址将失效。`, '删除订阅', { type: 'warning' }); await api(`/subscriptions/${s.id}`, 'DELETE'); await load() }) }
async function copy(value: string) { await action(async () => { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(value); ElMessage.success('已复制') } else { preview.value = value; previewVisible.value = true; ElMessage.info('当前环境不支持自动复制，请手动复制') } }) }
async function showConfig(s: Subscription) { await action(async () => { const path = new URL(s.clash_url).pathname; const res = await fetch(path); if (!res.ok) throw new Error((await res.json()).detail || '配置获取失败'); preview.value = await res.text(); previewVisible.value = true }) }
function downloadPreview() { const url = URL.createObjectURL(new Blob([preview.value], { type: 'application/yaml' })); const a = document.createElement('a'); a.href = url; a.download = 'ghelper.yaml'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
async function probe() { await action(async () => { const list = selectedNodes.value.length ? selectedNodes.value : filteredNodes.value; await api('/probes', 'POST', { node_ids: list.map(n => n.id) }); await load(); schedulePoll(); ElMessage.success('测速任务已提交') }) }
onMounted(async () => {
  window.addEventListener('session-expired', expire)
  try { const user = await api('/auth/me'); setCsrf(user.csrf); loggedIn.value = true; await load(); schedulePoll() } catch { /* Login form handles unauthenticated startup. */ }
  booting.value = false
})
onUnmounted(() => { clearTimeout(timer); window.removeEventListener('session-expired', expire) })
</script>

<template>
  <div v-if="booting" class="boot"><el-icon class="is-loading" :size="32"><Refresh /></el-icon><p>正在加载管理台</p></div>
  <main v-else-if="!loggedIn" class="login-page">
    <div class="login-story"><span class="brand"><el-icon><Connection /></el-icon> GHelper<span class="brand-small"> / WEB</span></span><div><span class="eyebrow">YOUR CONNECTIONS, TOGETHER</span><h1>每一个连接，<br>尽在掌握。</h1><p>集中管理账号与节点，<br>用一个订阅连接你的 Clash。</p><div class="login-line"><span></span><el-icon><Connection /></el-icon><span></span></div></div><small>GHelper Clash Web · 个人连接管理台</small></div>
    <div class="login-side"><section class="login-box"><span class="eyebrow">WELCOME BACK</span><h2>登录管理台</h2><p class="muted">使用初始化配置中的管理员账号。</p><el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="mb" /><el-form label-position="top" @submit.prevent="login"><el-form-item label="管理员账号"><el-input v-model="credentials.username" autocomplete="username" size="large" /></el-form-item><el-form-item label="密码"><el-input v-model="credentials.password" type="password" show-password autocomplete="current-password" size="large" /></el-form-item><el-button type="primary" size="large" native-type="submit" class="full" :loading="busy">登录 <el-icon><ArrowRight /></el-icon></el-button></el-form><p class="login-foot">初次使用？运行 <code>python -m app.cli init</code><br>在本机 .env 文件中查看登录信息。</p></section></div>
  </main>
  <div v-else class="layout">
    <aside class="sidebar"><div class="brand"><el-icon><Connection /></el-icon> GHelper<span class="brand-small">WEB</span></div><div class="nav-caption">工作空间</div><nav aria-label="主导航"><button v-for="item in nav" :key="item.id" :class="['nav-item', { active: active === item.id }]" :aria-label="item.label" :aria-current="active === item.id ? 'page' : undefined" @click="active = item.id"><el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span><span v-if="item.id === 'accounts'" class="nav-count">{{ accounts.length }}</span></button></nav><div class="sidebar-bottom"><span class="status-dot"></span> 独立部署 · 数据本地保存<p>GHelper Clash Web 1.0</p></div></aside>
    <div class="workspace"><header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <strong>{{ current.label }}</strong></div><div class="top-actions"><span class="admin-badge">A</span><span>管理员</span><el-button text :icon="SwitchButton" @click="logout">退出</el-button></div></header>
      <main class="content"><el-alert v-if="error" :title="error" type="error" show-icon role="alert" class="mb" @close="error = ''" />
        <div class="page-heading"><div><span class="eyebrow">GHELPER WORKSPACE</span><h1>{{ current.label }}</h1><p class="muted">{{ active === 'overview' ? '账号、节点与订阅，一个清晰的连接视图。' : active === 'accounts' ? '添加多个 token，独立管理每个账号的连接状态。' : active === 'nodes' ? '查看入口与连通性，找到适合当前网络的节点。' : active === 'subscriptions' ? '组合你的账号，生成可自动更新的 Clash 订阅。' : active === 'jobs' ? '最近 7 天的手动刷新与测速任务。' : '调整获取频率，查看当前部署信息。' }}</p></div><el-button v-if="['overview', 'accounts'].includes(active)" type="primary" :icon="Plus" @click="importVisible = true">添加账号</el-button><el-button v-if="active === 'subscriptions'" type="primary" :icon="Plus" :disabled="!accounts.length" @click="newSub()">创建订阅</el-button></div>
        <div v-if="running.length" class="running-banner" role="status"><el-icon class="is-loading"><Refresh /></el-icon><span>{{ running.length }} 个任务进行中</span><el-progress :percentage="Math.round(running.reduce((a, j) => a + j.completed, 0) / Math.max(1, running.reduce((a, j) => a + j.total, 0)) * 100)" :stroke-width="6" /><button class="text-link" @click="active = 'jobs'">查看进度 →</button></div>

        <template v-if="active === 'overview'">
          <section class="stats-grid"><article class="stat"><span>管理账号</span><div>{{ accounts.length }} <small>个</small></div><p><span class="status-dot"></span>{{ healthy }} 个已就绪</p><el-icon><Key /></el-icon></article><article class="stat"><span>当前节点</span><div>{{ usable.length }} <small>个</small></div><p>{{ regions.length }} 个入口代号</p><el-icon><Connection /></el-icon></article><article class="stat"><span>订阅配置</span><div>{{ subscriptions.length }} <small>份</small></div><p>独立订阅与多账号聚合</p><el-icon><Link /></el-icon></article><article class="stat"><span>自动刷新</span><div>{{ settings.refresh_minutes }} <small>分钟</small></div><p>服务端决定会话是否轮换</p><el-icon><Refresh /></el-icon></article></section>
          <section class="panel"><div class="panel-heading"><div><h2>账号连接状态</h2><p class="muted">最近获取结果与节点数量</p></div><el-button :icon="Refresh" :loading="busy" :disabled="!accounts.length" @click="refresh()">刷新全部</el-button></div><el-empty v-if="!accounts.length" description="还没有账号，添加第一个 token 开始使用"><el-button type="primary" @click="importVisible = true">添加账号</el-button></el-empty><div v-else class="account-overview"><div v-for="a in accounts" :key="a.id" class="account-row"><div class="account-avatar"><el-icon><Key /></el-icon></div><div class="grow"><strong>{{ a.name }}</strong><p class="muted">{{ a.info.email || a.token_hint }}</p></div><el-tag :type="a.enabled && a.status === 'ready' ? 'success' : a.status === 'invalid' || a.status === 'error' ? 'danger' : 'info'" effect="light">{{ !a.enabled ? '已停用' : stateLabel[a.status] }}</el-tag><span class="node-counter">{{ a.node_count }} 节点</span><el-button text type="primary" @click="active = 'accounts'">管理 <el-icon><ArrowRight /></el-icon></el-button></div></div></section>
          <div class="guide-grid"><article class="guide-card"><el-icon :size="28"><Link /></el-icon><h3>让配置自动更新</h3><p>创建订阅并复制地址到 Clash 客户端。服务会定期获取节点，无需重复导入文件。</p><button class="text-link" @click="active = 'subscriptions'">前往订阅管理 →</button></article><article class="guide-card soft"><el-icon :size="28"><Connection /></el-icon><h3>入口不等于出口</h3><p>广州、佛山等代号表示国内接入入口。测速反映本服务所在网络的连接耗时，不代表带宽。</p><button class="text-link" @click="active = 'nodes'">查看节点详情 →</button></article></div>
        </template>

        <template v-if="active === 'accounts'">
          <div class="toolbar"><span class="muted">共 {{ accounts.length }} 个账号</span><el-button :icon="Refresh" :disabled="!accounts.length" :loading="busy" @click="refresh()">刷新全部</el-button></div>
          <el-empty v-if="!accounts.length" description="粘贴 token 或导入本地脚本生成的 JSON"><el-button type="primary" @click="importVisible = true">添加账号</el-button></el-empty>
          <div class="account-grid"><article v-for="a in accounts" :key="a.id" class="panel account-card"><div class="card-top"><div class="account-avatar"><el-icon><Key /></el-icon></div><div class="grow"><h2>{{ a.name }}</h2><code class="muted">{{ a.token_hint }}</code></div><el-tag :type="a.status === 'ready' && a.enabled ? 'success' : a.status === 'error' || a.status === 'invalid' ? 'danger' : 'info'">{{ !a.enabled ? '已停用' : stateLabel[a.status] }}</el-tag></div><el-alert v-if="a.error || a.info_error" :title="a.error || a.info_error || ''" type="warning" :closable="false" show-icon class="mb" /><dl class="account-details"><div><dt>账号邮箱</dt><dd>{{ a.info.email || '上游未提供' }}</dd></div><div><dt>会员状态</dt><dd>{{ a.info.is_vip === true ? 'VIP' : a.info.is_vip === false ? '普通账号' : '上游未提供' }}</dd></div><div><dt>会员日期</dt><dd>{{ a.info.vip_date || '上游未提供' }}</dd></div><div><dt>浏览器累计流量</dt><dd>{{ bytes(a.info.browser_traffic_total) }}</dd></div><div><dt>节点 / 会话</dt><dd>{{ a.node_count }} / {{ a.pk_hint || '—' }}</dd></div><div><dt>会话首次发现</dt><dd>{{ time(a.first_seen) }}</dd></div><div><dt>最近成功获取</dt><dd>{{ time(a.last_success) }}</dd></div></dl><div class="mb"><el-tag v-if="a.stale" type="warning">缓存待更新</el-tag><el-tag v-if="a.degraded" type="warning">仅解析主节点</el-tag></div><div class="card-actions"><el-button :loading="busy" :disabled="!a.enabled || a.status === 'invalid'" @click="refresh(a.id)">刷新</el-button><el-button @click="editAccount(a)">编辑</el-button><el-button type="primary" plain @click="newSub(a)">生成订阅</el-button><el-button text type="danger" @click="removeAccount(a)">删除</el-button></div></article></div>
        </template>

        <section v-if="active === 'nodes'" class="panel">
          <div class="filters"><el-input v-model="search" placeholder="搜索节点或账号" :prefix-icon="Search" clearable aria-label="搜索节点" /><el-select v-model="accountFilter" placeholder="全部账号" clearable aria-label="筛选账号"><el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" /></el-select><el-select v-model="regionFilter" placeholder="全部入口" clearable aria-label="筛选入口"><el-option v-for="r in regions" :key="r" :label="r" :value="r" /></el-select><el-select v-model="statusFilter" placeholder="全部状态" clearable aria-label="筛选状态"><el-option label="已测通" value="ok" /><el-option label="连接失败" value="failed" /><el-option label="未测速" value="untested" /></el-select><el-button type="primary" :loading="busy" :disabled="!filteredNodes.length || running.some(j => j.kind === 'probe')" @click="probe">{{ selectedNodes.length ? `测速选中 (${selectedNodes.length})` : '测速当前列表' }}</el-button></div>
          <el-table :data="filteredNodes" row-key="id" @selection-change="(v: Node[]) => selectedNodes = v" empty-text="暂无节点，请先添加账号并刷新" v-loading="loading"><el-table-column type="selection" width="42" /><el-table-column label="入口" prop="region" width="110" sortable /><el-table-column label="节点地址" min-width="260"><template #default="{ row }"><code class="server">{{ row.server }}</code><div class="muted">HTTPS · {{ row.port }}</div></template></el-table-column><el-table-column label="来源账号" prop="account_name" min-width="125" /><el-table-column label="连接状态" width="120"><template #default="{ row }"><el-tag :type="row.stale || !row.enabled ? 'info' : row.reachable === true ? 'success' : row.reachable === false ? 'danger' : 'info'">{{ !row.enabled ? '已停用' : row.stale ? '快照过期' : row.reachable === true ? '已测通' : row.reachable === false ? '连接失败' : '未测速' }}</el-tag></template></el-table-column><el-table-column label="延迟" prop="latency" sortable width="110"><template #default="{ row }">{{ row.latency === null ? '—' : `${row.latency} ms` }}</template></el-table-column><el-table-column label="测速时间" width="180"><template #default="{ row }">{{ time(row.tested_at) }}</template></el-table-column></el-table><div class="table-footer">{{ filteredNodes.length }} 个节点 · 测速请求经节点代理访问 204 检测地址，不进行带宽测试。</div>
        </section>

        <template v-if="active === 'subscriptions'"><el-empty v-if="!subscriptions.length" description="创建第一个订阅，将节点交给 Clash 自动管理"><el-button type="primary" :disabled="!accounts.length" @click="newSub()">创建订阅</el-button></el-empty><div class="subscription-grid"><article v-for="s in subscriptions" :key="s.id" class="panel sub-card"><div class="card-top"><div class="account-avatar"><el-icon><Link /></el-icon></div><div class="grow"><h2>{{ s.name }}</h2><p class="muted">{{ s.account_ids.length }} 个来源账号 · {{ s.regions.length ? s.regions.join(' / ') : '全部入口' }}</p></div><el-tag :type="s.enabled ? 'success' : 'info'">{{ s.enabled ? '已启用' : '已停用' }}</el-tag></div><div class="source-tags"><el-tag v-for="id in s.account_ids" :key="id" type="info">{{ accounts.find(a => a.id === id)?.name || '已删除账号' }}</el-tag></div><label class="field-label">Clash 完整配置</label><div class="subscription-url"><span>{{ settings.public_base_url }}/sub/••••••••/clash.yaml</span><el-button text type="primary" @click="copy(s.clash_url)">复制地址</el-button></div><div class="card-actions"><el-button :icon="Download" :disabled="!s.enabled" @click="showConfig(s)">预览 / 下载</el-button><el-button @click="copy(s.proxies_url)">Provider 地址</el-button><el-button text @click="editSub(s)">编辑</el-button><el-button text @click="rotate(s)">轮换密钥</el-button><el-button text type="danger" @click="removeSub(s)">删除</el-button></div></article></div><div class="note">在 Clash 中新建远程订阅并粘贴地址，建议客户端每 30–60 分钟更新。订阅地址具有访问权限，请妥善保存。</div></template>

        <section v-if="active === 'jobs'" class="panel"><el-table :data="jobs" empty-text="暂无任务记录"><el-table-column label="任务" width="130"><template #default="{ row }">{{ row.kind === 'refresh' ? '账号刷新' : '节点测速' }}</template></el-table-column><el-table-column label="状态" width="125"><template #default="{ row }"><el-tag :type="row.status === 'completed' ? 'success' : ['partial', 'interrupted'].includes(row.status) ? 'warning' : 'info'">{{ stateLabel[row.status] || row.status }}</el-tag></template></el-table-column><el-table-column label="进度" min-width="180"><template #default="{ row }"><el-progress :percentage="Math.round(row.completed / Math.max(1, row.total) * 100)" /><small>{{ row.completed }} / {{ row.total }}</small></template></el-table-column><el-table-column label="创建时间" width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="详情" min-width="200"><template #default="{ row }"><span class="muted">{{ row.errors.slice(0, 3).join('；') || '—' }}{{ row.errors.length > 3 ? ` 等 ${row.errors.length} 项` : '' }}</span></template></el-table-column></el-table></section>

        <section v-if="active === 'settings'" class="panel settings-panel"><h2>获取设置</h2><p class="muted">自动刷新只查询当前会话，不能强制上游签发新节点。</p><el-form label-position="top" @submit.prevent="saveSettings"><el-form-item label="自动刷新间隔（分钟）"><el-input-number v-model="settings.refresh_minutes" :min="5" :max="30" /></el-form-item><el-form-item label="对外访问地址"><el-input :model-value="settings.public_base_url" readonly /><small class="muted">通过环境变量 PUBLIC_BASE_URL 配置；远程访问时请填写实际域名。</small></el-form-item><el-form-item label="兼容插件版本"><el-input :model-value="settings.extension_version" readonly /></el-form-item><el-button type="primary" native-type="submit" :loading="busy">保存设置</el-button></el-form><div class="note">数据库及加密密钥需要一起备份。管理员密码与 API 密钥通过 .env 配置，修改后重启服务。</div><a href="/api/docs" target="_blank" rel="noreferrer" class="text-link">查看 API 文档 →</a></section>
      </main><footer class="page-footer">GHelper Clash Web <span>连接由你掌控</span></footer>
    </div>
  </div>

  <el-dialog v-model="importVisible" title="添加账号" width="580px" @closed="importText = ''"><p class="muted">每行一个 token，也可填写「名称,token」或粘贴导出 JSON。</p><el-input v-model="importText" type="textarea" :rows="8" placeholder="账号 A,32 位 token&#10;账号 B,32 位 token" aria-label="批量 token" autocomplete="off" /><label class="file-picker">导入本地 JSON <input type="file" accept=".json,application/json" @change="readImport" /></label><template #footer><el-button @click="importVisible = false">取消</el-button><el-button type="primary" :loading="busy" @click="importAccounts">导入账号</el-button></template></el-dialog>
  <el-dialog v-model="accountVisible" title="编辑账号" width="500px" @closed="editingAccount.token = ''"><el-form label-position="top"><el-form-item label="账号名称"><el-input v-model="editingAccount.name" maxlength="80" /></el-form-item><el-form-item label="替换 token（留空保持原值）"><el-input v-model="editingAccount.token" type="password" show-password autocomplete="off" /></el-form-item><el-form-item label="参与刷新和订阅"><el-switch v-model="editingAccount.enabled" /></el-form-item></el-form><template #footer><el-button @click="accountVisible = false">取消</el-button><el-button type="primary" :loading="busy" @click="saveAccount">保存</el-button></template></el-dialog>
  <el-dialog v-model="subVisible" :title="subForm.id ? '编辑订阅' : '创建订阅'" width="560px"><el-form label-position="top"><el-form-item label="订阅名称"><el-input v-model="subForm.name" maxlength="80" /></el-form-item><el-form-item label="来源账号"><el-select v-model="subForm.account_ids" multiple placeholder="选择一个或多个账号" class="full"><el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}${a.enabled ? '' : '（已停用）'}`" /></el-select></el-form-item><el-form-item label="入口筛选（留空包含全部）"><el-select v-model="subForm.regions" multiple filterable class="full"><el-option v-for="r in regions" :key="r" :value="r" :label="r" /></el-select></el-form-item><el-form-item label="排除最近 30 分钟测速失败的节点"><el-switch v-model="subForm.exclude_failed" /></el-form-item><el-form-item label="启用订阅"><el-switch v-model="subForm.enabled" /></el-form-item></el-form><template #footer><el-button @click="subVisible = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!subForm.account_ids.length || !subForm.name.trim()" @click="saveSub">保存订阅</el-button></template></el-dialog>
  <el-dialog v-model="previewVisible" title="配置预览" width="850px" @closed="preview = ''"><pre class="config-preview">{{ preview }}</pre><template #footer><el-button @click="copy(preview)">复制内容</el-button><el-button type="primary" :icon="Download" @click="downloadPreview">下载 YAML</el-button></template></el-dialog>
</template>
