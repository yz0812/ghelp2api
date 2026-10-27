<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Files, Grid, Key, Link, Tickets, Setting, Plus, Refresh, Search, Download, SwitchButton, ArrowRight, Delete, QuestionFilled } from '@element-plus/icons-vue'
import { api, setCsrf } from './api'
import type { Account, Node, Source, Subscription, SubNode, Job } from './types'

const loggedIn = ref(false), booting = ref(true), loading = ref(false), busy = ref(false)
const active = ref('overview'), error = ref('')
const credentials = reactive({ username: 'admin', password: '' })
const accounts = ref<Account[]>([]), nodes = ref<Node[]>([]), subscriptions = ref<Subscription[]>([]), jobs = ref<Job[]>([])
const sources = ref<Source[]>([])
const settings = reactive({ refresh_minutes: 30, probe_minutes: 30, probe_timeout_ms: 5000, mihomo: false, public_base_url: '', extension_version: '' })
const search = ref(''), accountFilter = ref(''), regionFilter = ref(''), statusFilter = ref('')
const selectedNodes = ref<Node[]>([])
const importVisible = ref(false), importText = ref(''), accountVisible = ref(false)
const editingAccount = reactive({ id: '', name: '', token: '', enabled: true })
const sourceVisible = ref(false), sourceForm = reactive({ id: '', name: '', url: '', text: '', enabled: true })
const subVisible = ref(false), subForm = reactive({ id: '', name: '', account_ids: [] as string[], source_ids: [] as string[], regions: [] as string[], exclude_failed: false, dedupe: true, max_latency: null as number | null, max_per_country: null as number | null, enabled: true })
const previewVisible = ref(false), preview = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
const nav = [
  { id: 'overview', label: '连接总览', icon: Grid }, { id: 'accounts', label: '账号管理', icon: Key },
  { id: 'nodes', label: '节点列表', icon: Connection }, { id: 'sources', label: '订阅源', icon: Files },
  { id: 'subscriptions', label: '订阅管理', icon: Link },
  { id: 'jobs', label: '任务记录', icon: Tickets }, { id: 'settings', label: '系统设置', icon: Setting }
]
const current = computed(() => nav.find(n => n.id === active.value)!)
const healthy = computed(() => accounts.value.filter(a => a.enabled && a.status === 'ready').length)
const usable = computed(() => nodes.value.filter(n => n.enabled && !n.stale))
const regions = computed(() => [...new Set(nodes.value.map(n => n.region))].sort())
const regionLabels = computed(() => Object.fromEntries(nodes.value.map(n => [n.region, n.region_label])))
function regionText(code: string) { return regionLabels.value[code] || code }
const filteredNodes = computed(() => nodes.value.filter(n =>
  (!accountFilter.value || n.account_id === accountFilter.value) &&
  (!regionFilter.value || n.region === regionFilter.value) &&
  (!search.value || `${n.server} ${n.account_name} ${n.region} ${n.region_label}`.toLowerCase().includes(search.value.toLowerCase())) &&
  (!statusFilter.value || (statusFilter.value === 'ok' ? n.reachable === true : statusFilter.value === 'failed' ? n.reachable === false : n.reachable === null))))
const running = computed(() => jobs.value.filter(j => ['pending', 'running'].includes(j.status)))
const nodePage = ref(1), nodePageSize = ref(20), jobPage = ref(1), jobPageSize = ref(20)
const pagedNodes = computed(() => filteredNodes.value.slice((nodePage.value - 1) * nodePageSize.value, nodePage.value * nodePageSize.value))
const pagedJobs = computed(() => jobs.value.slice((jobPage.value - 1) * jobPageSize.value, jobPage.value * jobPageSize.value))
const sourcePage = ref(1), sourcePageSize = 9
const checkingSources = computed(() => running.value.some(j => j.kind === 'source'))
const invalidSources = computed(() => sources.value.filter(s => s.invalid))
const pagedSources = computed(() => sources.value.slice((sourcePage.value - 1) * sourcePageSize, sourcePage.value * sourcePageSize))
const contentRef = ref<HTMLElement>()
watch([search, accountFilter, regionFilter, statusFilter], () => { nodePage.value = 1 })
watch([nodePageSize, () => filteredNodes.value.length], () => { nodePage.value = Math.min(nodePage.value, Math.max(1, Math.ceil(filteredNodes.value.length / nodePageSize.value))) })
watch([jobPageSize, () => jobs.value.length], () => { jobPage.value = Math.min(jobPage.value, Math.max(1, Math.ceil(jobs.value.length / jobPageSize.value))) })
watch([() => sources.value.length], () => { sourcePage.value = Math.min(sourcePage.value, Math.max(1, Math.ceil(sources.value.length / sourcePageSize))) })
watch(active, () => contentRef.value?.scrollTo({ top: 0 }))
const stateLabel: Record<string, string> = { pending: '等待获取', ready: '已就绪', error: '获取失败', invalid: '凭证失效', running: '进行中', completed: '已完成', partial: '部分失败', interrupted: '已中断' }
const jobKind: Record<string, string> = { refresh: '账号刷新', probe: '节点测速', sub_probe: '订阅测速', source: '订阅源检测' }
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
    const [a, n, src, s, j, config] = await Promise.all([api('/accounts'), api('/nodes'), api('/sources'), api('/subscriptions'), api('/jobs'), api('/settings')])
    accounts.value = a; nodes.value = n; sources.value = src; subscriptions.value = s; jobs.value = j; Object.assign(settings, config)
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
async function saveSettings() { await action(async () => { await api('/settings', 'PUT', { refresh_minutes: settings.refresh_minutes, probe_minutes: settings.probe_minutes, probe_timeout_ms: settings.probe_timeout_ms }); ElMessage.success('设置已保存') }) }
async function login() { await action(async () => { const r = await api('/auth/login', 'POST', credentials); setCsrf(r.csrf); credentials.password = ''; loggedIn.value = true; await load(); schedulePoll() }) }
async function logout() { await action(async () => { await api('/auth/logout', 'POST'); expire() }) }
function expire() { loggedIn.value = false; accounts.value = []; nodes.value = []; sources.value = []; subscriptions.value = []; clearTimeout(timer); setCsrf('') }
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
function newSub(account?: Account) { Object.assign(subForm, { id: '', name: account ? `${account.name} 订阅` : '我的统一订阅', account_ids: account ? [account.id] : accounts.value.filter(a => a.enabled).map(a => a.id), source_ids: [], regions: [], exclude_failed: false, dedupe: true, max_latency: null, max_per_country: null, enabled: true }); subVisible.value = true }
function editSub(s: Subscription) { Object.assign(subForm, { ...s, account_ids: [...s.account_ids], source_ids: [...s.source_ids], regions: [...s.regions] }); subVisible.value = true }
async function saveSub() { await action(async () => { const { id, name, account_ids, source_ids, regions, exclude_failed, dedupe, max_latency, max_per_country, enabled } = subForm; await api(id ? `/subscriptions/${id}` : '/subscriptions', id ? 'PUT' : 'POST', { name, account_ids, source_ids, regions, exclude_failed, dedupe, max_latency: max_latency || null, max_per_country: max_per_country || null, enabled }); subVisible.value = false; await load(); ElMessage.success('订阅已保存') }) }
function hostOf(address: string) { try { return new URL(address).hostname } catch { return '' } }
function newSource() { Object.assign(sourceForm, { id: '', name: '', url: '', text: '', enabled: true }); sourceVisible.value = true }
function editSource(s: Source) { Object.assign(sourceForm, { id: s.id, name: s.name, url: '', text: '', enabled: s.enabled }); sourceVisible.value = true }
async function saveSource() {
  await action(async () => {
    const { id, name, url, enabled } = sourceForm
    if (id) {
      await api(`/sources/${id}`, 'PUT', { name, enabled, ...(url ? { url } : {}) })
    } else {
      const entries = sourceForm.text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((line, index) => {
        const parts = line.split(/[,，\s]+/).filter(Boolean)
        const address = parts.length > 1 ? parts.at(-1)! : line
        return { name: (parts.length > 1 ? parts.slice(0, -1).join(' ') : hostOf(address) || `订阅源 ${index + 1}`).slice(0, 80), url: address }
      })
      if (!entries.length) throw new Error('请填写至少一个订阅地址')
      const created = await api('/sources/import', 'POST', { sources: entries })
      ElMessage.success(`已添加 ${created.length} 个订阅源`)
    }
    sourceVisible.value = false; sourceForm.text = ''; sourceForm.url = ''; await load()
  })
}
const fileVisible = ref(false), fileForm = reactive({ name: '', content: '', filename: '' })
let reuploadTarget: Source | undefined
const reuploadInput = ref<HTMLInputElement>()
async function readSourceFile(file: File | undefined) {
  if (!file) return ''
  if (file.size > 10_000_000) throw new Error('文件不能超过 10 MB')
  return await file.text()
}
function newSourceFile() { Object.assign(fileForm, { name: '', content: '', filename: '' }); fileVisible.value = true }
async function pickSourceFile(event: Event) {
  const input = event.target as HTMLInputElement
  await action(async () => {
    const file = input.files?.[0]
    fileForm.content = await readSourceFile(file)
    fileForm.filename = file?.name || ''
    if (file && !fileForm.name.trim()) fileForm.name = file.name.replace(/\.(ya?ml|txt|conf)$/i, '').slice(0, 80)
  })
  input.value = ''
}
async function saveSourceFile() {
  await action(async () => {
    const created = await api('/sources/file', 'POST', { name: fileForm.name.trim(), content: fileForm.content })
    fileVisible.value = false; await load()
    ElMessage.success(`已导入「${created.name}」：${created.node_count} 个节点`)
  })
}
function reuploadSource(s: Source) { reuploadTarget = s; reuploadInput.value?.click() }
async function pickReupload(event: Event) {
  const input = event.target as HTMLInputElement
  await action(async () => {
    const content = await readSourceFile(input.files?.[0])
    if (!content || !reuploadTarget) return
    const result = await api(`/sources/${reuploadTarget.id}/file`, 'PUT', { content })
    await load(); ElMessage.success(`已更新「${result.name}」：${result.node_count} 个节点`)
  })
  input.value = ''; reuploadTarget = undefined
}
async function refreshSource(s: Source) { await action(async () => { const result = await api(`/sources/${s.id}/refresh`, 'POST'); await load(); ElMessage.success(`已获取 ${result.node_count} 个节点`) }) }
async function removeSource(s: Source) { await action(async () => { await ElMessageBox.confirm(`删除「${s.name}」？使用它的订阅会移除该来源。`, '删除订阅源', { type: 'warning' }); await api(`/sources/${s.id}`, 'DELETE'); await load() }) }
let checkJobId = ''
async function checkSources() {
  await action(async () => {
    const result = await api('/sources/check', 'POST')
    checkJobId = result.job_id
    await load(); schedulePoll(); ElMessage.info('已开始检测订阅源，完成后会提示结果')
  })
}
watch(jobs, list => {
  if (!checkJobId) return
  const job = list.find(j => j.id === checkJobId)
  if (!job || !['completed', 'partial', 'interrupted'].includes(job.status)) return
  checkJobId = ''
  const bad = invalidSources.value.length
  ElMessage({ type: bad ? 'warning' : 'success', message: bad ? `检测完成：${bad} 个订阅源失效` : '检测完成：全部订阅源正常' })
})
async function disableInvalid() {
  await action(async () => {
    const count = invalidSources.value.length
    await ElMessageBox.confirm(`停用 ${count} 个失效订阅源？停用后不再参与订阅生成，可随时重新启用。`, '一键禁用失效', { type: 'warning' })
    const result = await api('/sources/invalid/disable', 'POST'); await load()
    ElMessage.success(`已停用 ${result.disabled} 个失效订阅源`)
  })
}
async function deleteInvalid() {
  await action(async () => {
    const count = invalidSources.value.length
    await ElMessageBox.confirm(`删除 ${count} 个失效订阅源？引用它们的订阅会移除该来源，此操作不可撤销。`, '一键删除失效', { type: 'warning' })
    const result = await api('/sources/invalid/delete', 'POST'); await load()
    ElMessage.success(`已删除 ${result.deleted} 个失效订阅源`)
  })
}
async function rotate(s: Subscription) { await action(async () => { await ElMessageBox.confirm('轮换后旧订阅地址立即失效，客户端需要重新添加地址。', '轮换订阅密钥', { type: 'warning' }); await api(`/subscriptions/${s.id}/rotate`, 'POST'); await load() }) }
async function removeSub(s: Subscription) { await action(async () => { await ElMessageBox.confirm(`删除「${s.name}」？原订阅地址将失效。`, '删除订阅', { type: 'warning' }); await api(`/subscriptions/${s.id}`, 'DELETE'); await load() }) }
const expandedSubs = ref<string[]>([])
function toggleSubSources(id: string) { expandedSubs.value = expandedSubs.value.includes(id) ? expandedSubs.value.filter(x => x !== id) : [...expandedSubs.value, id] }
function visibleSources(s: Subscription) { return expandedSubs.value.includes(s.id) ? s.source_ids : [] }
async function copy(value: string) { await action(async () => { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(value); ElMessage.success('已复制') } else { preview.value = value; previewVisible.value = true; ElMessage.info('当前环境不支持自动复制，请手动复制') } }) }
async function showConfig(s: Subscription) { await action(async () => { const path = new URL(s.clash_url).pathname; const res = await fetch(path); if (!res.ok) throw new Error((await res.json()).detail || '配置获取失败'); preview.value = await res.text(); previewVisible.value = true }) }
function downloadPreview() { const url = URL.createObjectURL(new Blob([preview.value], { type: 'application/yaml' })); const a = document.createElement('a'); a.href = url; a.download = 'ghelper.yaml'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
async function probe() { await action(async () => { const list = selectedNodes.value.length ? selectedNodes.value : filteredNodes.value; await api('/probes', 'POST', { node_ids: list.map(n => n.id) }); await load(); schedulePoll(); ElMessage.success('测速任务已提交') }) }
const detailVisible = ref(false), detailSub = ref<Subscription>(), detailNodes = ref<SubNode[]>([]), detailLoading = ref(false), detailJobId = ref('')
const detailSearch = ref(''), detailCountry = ref(''), detailOrigin = ref(''), detailStatus = ref(''), detailSort = reactive({ prop: 'latency', order: 'ascending' as 'ascending' | 'descending' | null }), detailSelected = ref<SubNode[]>([]), detailPage = ref(1), detailPageSize = ref(50)
let detailTimer: ReturnType<typeof setTimeout> | undefined
const detailCountries = computed(() => [...new Map(detailNodes.value.map(n => [n.country, n.country_label])).entries()].sort((a, b) => a[1].localeCompare(b[1], 'zh-CN')))
const detailOrigins = computed(() => [...new Set(detailNodes.value.map(n => n.origin))].sort((a, b) => a.localeCompare(b, 'zh-CN')))
// 分页前对整份列表排序：el-table 自带排序只排当前页
function sortValue(n: SubNode, prop: string): number | string | null { return prop === 'latency' ? n.latency : prop === 'tested_at' ? n.tested_at : prop === 'country_label' ? n.country_label : prop === 'origin' ? n.origin : n.name }
function compareNodes(a: SubNode, b: SubNode) {
  const x = sortValue(a, detailSort.prop), y = sortValue(b, detailSort.prop)
  if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1  // 未测速的始终排在最后
  const result = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'zh-CN')
  return detailSort.order === 'descending' ? -result : result
}
const detailFiltered = computed(() => detailNodes.value.filter(n =>
  (!detailCountry.value || n.country === detailCountry.value) &&
  (!detailOrigin.value || n.origin === detailOrigin.value) &&
  (!detailSearch.value || `${n.name} ${n.server} ${n.group}`.toLowerCase().includes(detailSearch.value.toLowerCase())) &&
  (!detailStatus.value || (detailStatus.value === 'included' ? n.included : detailStatus.value === 'excluded' ? !n.included : detailStatus.value === 'ok' ? n.reachable === true : detailStatus.value === 'failed' ? n.reachable === false : n.reachable === null))).sort((a, b) => detailSort.order ? compareNodes(a, b) : 0))
const detailPaged = computed(() => detailFiltered.value.slice((detailPage.value - 1) * detailPageSize.value, detailPage.value * detailPageSize.value))
const detailStats = computed(() => {
  const tested = detailNodes.value.filter(n => n.latency !== null).map(n => n.latency!).sort((a, b) => a - b)
  return { total: detailNodes.value.length, included: detailNodes.value.filter(n => n.included).length, ok: detailNodes.value.filter(n => n.reachable === true).length, failed: detailNodes.value.filter(n => n.reachable === false).length, untested: detailNodes.value.filter(n => n.reachable === null).length, median: tested.length ? tested[Math.floor(tested.length / 2)] : null }
})
const detailJob = computed(() => jobs.value.find(j => j.id === detailJobId.value))
watch([detailSearch, detailCountry, detailOrigin, detailStatus, detailPageSize, () => detailSort.prop, () => detailSort.order], () => { detailPage.value = 1 })
function sortDetail({ prop, order }: { prop: string, order: 'ascending' | 'descending' | null }) { detailSort.prop = prop; detailSort.order = order }
function latencyClass(n: SubNode) { return n.latency === null ? '' : n.latency <= 200 ? 'latency-good' : n.latency <= 500 ? 'latency-fair' : 'latency-bad' }
async function loadDetail() {
  if (!detailSub.value) return
  const result = await api(`/subscriptions/${detailSub.value.id}/nodes`)
  detailNodes.value = result.nodes; detailJobId.value = result.job_id || ''
  clearTimeout(detailTimer)
  if (detailJobId.value && detailVisible.value) detailTimer = setTimeout(() => { loadDetail().catch(e => { error.value = e.message }) }, 2000)
}
async function openDetail(s: Subscription) {
  detailSearch.value = ''; detailCountry.value = ''; detailOrigin.value = ''; Object.assign(detailSort, { prop: 'latency', order: 'ascending' }); detailStatus.value = ''; detailSelected.value = []; detailPage.value = 1
  detailSub.value = s; detailNodes.value = []; detailVisible.value = true; detailLoading.value = true
  try { await loadDetail() } catch (e: any) { ElMessage.error(e.message) } finally { detailLoading.value = false }
}
function closeDetail() { clearTimeout(detailTimer); detailJobId.value = ''; detailNodes.value = [] }
async function refreshSub(s: Subscription) {
  await action(async () => {
    ElMessage.info('正在重新拉取账号与订阅源…')
    const result = await api(`/subscriptions/${s.id}/refresh`, 'POST')
    ElMessage.success(result.count ? `已重新拉取，开始测速 ${result.count} 个节点；完成后订阅按规则使用新结果` : '该订阅的测速仍在进行中')
    await load(); schedulePoll()
    if (detailVisible.value && detailSub.value?.id === s.id) await loadDetail()
  })
}
async function probeDetail() {
  await action(async () => {
    const keys = detailSelected.value.length ? detailSelected.value.map(n => n.key) : (detailFiltered.value.length < detailNodes.value.length ? detailFiltered.value.map(n => n.key) : null)
    const result = await api(`/subscriptions/${detailSub.value!.id}/probe`, 'POST', { keys })
    ElMessage.success(result.count ? `已开始测速 ${result.count} 个节点` : '该订阅的测速仍在进行中')
    await load(); schedulePoll(); await loadDetail()
  })
}
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
    <aside class="sidebar"><div class="brand"><el-icon><Connection /></el-icon> GHelper<span class="brand-small">WEB</span></div><div class="nav-caption">工作空间</div><nav aria-label="主导航"><button v-for="item in nav" :key="item.id" :class="['nav-item', { active: active === item.id }]" :aria-label="item.label" :aria-current="active === item.id ? 'page' : undefined" @click="active = item.id"><el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span><span v-if="item.id === 'accounts'" class="nav-count">{{ accounts.length }}</span></button></nav><div class="sidebar-bottom"><span class="status-dot"></span> 独立部署 · 数据本地保存<p>GHelper Clash Web 1.1</p></div></aside>
    <div class="workspace"><header class="topbar"><div class="breadcrumb">工作空间 <span>/</span> <strong>{{ current.label }}</strong></div><div class="top-actions"><span class="admin-badge">A</span><span>管理员</span><el-button text :icon="SwitchButton" @click="logout">退出</el-button></div></header>
      <main ref="contentRef" class="content"><el-alert v-if="error" :title="error" type="error" show-icon role="alert" class="mb" @close="error = ''" />
        <div class="page-heading"><div><span class="eyebrow">GHELPER WORKSPACE</span><h1>{{ current.label }}</h1><p class="muted">{{ active === 'overview' ? '账号、节点与订阅，一个清晰的连接视图。' : active === 'accounts' ? '添加多个 token，独立管理每个账号的连接状态。' : active === 'nodes' ? '查看入口与连通性，找到适合当前网络的节点。' : active === 'subscriptions' ? '组合账号与订阅源，生成可自动更新的统一订阅。' : active === 'sources' ? '导入其他机场或自建订阅，与 GHelper 节点合并导出。' : active === 'jobs' ? '最近 7 天的手动刷新、测速与订阅源检测任务。' : '调整获取频率，查看当前部署信息。' }}</p></div><el-button v-if="['overview', 'accounts'].includes(active)" type="primary" :icon="Plus" @click="importVisible = true">添加账号</el-button><el-button v-if="active === 'sources'" :icon="Files" @click="newSourceFile()">导入文件</el-button><el-button v-if="active === 'sources'" type="primary" :icon="Plus" @click="newSource()">添加订阅源</el-button><el-button v-if="active === 'subscriptions'" type="primary" :icon="Plus" :disabled="!accounts.length && !sources.length" @click="newSub()">创建订阅</el-button></div>
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

        <template v-if="active === 'sources'">
          <div class="toolbar"><span class="muted">共 {{ sources.length }} 个订阅源<template v-if="invalidSources.length"> · <span class="danger-text">{{ invalidSources.length }} 个失效</span></template></span><div class="toolbar-actions"><el-button :icon="Search" :disabled="!sources.length || checkingSources" :loading="busy" @click="checkSources">检测失效</el-button><el-button type="warning" plain :icon="SwitchButton" :disabled="!invalidSources.length || checkingSources" :loading="busy" @click="disableInvalid">一键禁用失效</el-button><el-button type="danger" plain :icon="Delete" :disabled="!invalidSources.length || checkingSources" :loading="busy" @click="deleteInvalid">一键删除失效</el-button></div></div>
          <el-empty v-if="!sources.length" description="粘贴其他机场的订阅地址，和 GHelper 节点一起导出"><el-button type="primary" @click="newSource()">添加订阅源</el-button></el-empty>
          <div v-else class="account-grid"><article v-for="s in pagedSources" :key="s.id" :class="['panel', 'account-card', { 'is-invalid': s.invalid }]"><div class="card-top"><div class="account-avatar"><el-icon><Files /></el-icon></div><div class="grow"><h2>{{ s.name }}</h2><code class="muted">{{ s.address }}</code></div><el-tag :type="s.invalid ? 'danger' : s.enabled ? 'success' : 'info'">{{ s.invalid ? '已失效' : s.enabled ? '已启用' : '已停用' }}</el-tag></div><el-alert v-if="s.last_error" :title="s.last_error" type="warning" :closable="false" show-icon class="mb" /><dl class="account-details"><div><dt>节点数量</dt><dd>{{ s.node_count }}</dd></div><div><dt>{{ s.kind === 'file' ? '导入时间' : '上次拉取' }}</dt><dd>{{ time(s.last_fetch) }}</dd></div><div><dt>连续失败</dt><dd>{{ s.failures }}</dd></div></dl><div class="card-actions"><el-button v-if="s.kind === 'file'" :loading="busy" @click="reuploadSource(s)">重新上传</el-button><el-button v-else :loading="busy" :disabled="!s.enabled" @click="refreshSource(s)">立即拉取</el-button><el-button @click="editSource(s)">编辑</el-button><el-button text type="danger" @click="removeSource(s)">删除</el-button></div></article></div>
          <div v-if="sources.length > sourcePageSize" class="table-footer"><span>第 {{ sourcePage }} / {{ Math.ceil(sources.length / sourcePageSize) }} 页 · 每页 {{ sourcePageSize }} 个</span><el-pagination v-model:current-page="sourcePage" :page-size="sourcePageSize" :pager-count="5" :total="sources.length" size="small" background layout="total, prev, pager, next" /></div>
          <input ref="reuploadInput" type="file" accept=".yaml,.yml,.txt,.conf" hidden @change="pickReupload" />
          <div class="note">文件导入的订阅源不会自动更新，节点变化后点「重新上传」。检测会重新拉取每个已启用的订阅源（并发 5），最近一次拉取失败的（地址过期、内容为空、没有可解析的节点）记为失效。停用后不再参与订阅生成，重新启用即可恢复。地址只保存在本地并加密存储，列表仅显示域名。</div>
        </template>

        <section v-if="active === 'nodes'" class="panel">
          <div class="filters"><el-input v-model="search" placeholder="搜索节点或账号" :prefix-icon="Search" clearable aria-label="搜索节点" /><el-select v-model="accountFilter" placeholder="全部账号" clearable aria-label="筛选账号"><el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" /></el-select><el-select v-model="regionFilter" placeholder="全部入口" clearable aria-label="筛选入口"><el-option v-for="r in regions" :key="r" :label="regionText(r)" :value="r" /></el-select><el-select v-model="statusFilter" placeholder="全部状态" clearable aria-label="筛选状态"><el-option label="已测通" value="ok" /><el-option label="连接失败" value="failed" /><el-option label="未测速" value="untested" /></el-select><el-button type="primary" :loading="busy" :disabled="!filteredNodes.length || running.some(j => j.kind === 'probe')" @click="probe">{{ selectedNodes.length ? `测速选中 (${selectedNodes.length})` : '测速当前列表' }}</el-button></div>
          <el-table :data="pagedNodes" row-key="id" max-height="100vh - 470px" @selection-change="(v: Node[]) => selectedNodes = v" empty-text="暂无节点，请先添加账号并刷新" v-loading="loading"><el-table-column type="selection" width="42" reserve-selection /><el-table-column label="入口" prop="region" width="140" sortable><template #default="{ row }">{{ regionText(row.region) }}</template></el-table-column><el-table-column label="节点地址" min-width="260"><template #default="{ row }"><code class="server">{{ row.server }}</code><div class="muted">HTTPS · {{ row.port }}</div></template></el-table-column><el-table-column label="来源账号" prop="account_name" min-width="125" /><el-table-column label="连接状态" width="120"><template #default="{ row }"><el-tag :type="row.stale || !row.enabled ? 'info' : row.reachable === true ? 'success' : row.reachable === false ? 'danger' : 'info'">{{ !row.enabled ? '已停用' : row.stale ? '快照过期' : row.reachable === true ? '已测通' : row.reachable === false ? '连接失败' : '未测速' }}</el-tag></template></el-table-column><el-table-column label="延迟" prop="latency" sortable width="110"><template #default="{ row }">{{ row.latency === null ? '—' : `${row.latency} ms` }}</template></el-table-column><el-table-column label="测速时间" width="180"><template #default="{ row }">{{ time(row.tested_at) }}</template></el-table-column></el-table><div class="table-footer"><span>{{ filteredNodes.length }} 个节点 · 测速请求经节点代理访问 204 检测地址，不进行带宽测试。</span><el-pagination v-model:current-page="nodePage" v-model:page-size="nodePageSize" :page-sizes="[20, 50, 100]" :pager-count="5" :total="filteredNodes.length" size="small" background layout="total, sizes, prev, pager, next" /></div>
        </section>

        <template v-if="active === 'subscriptions'"><el-empty v-if="!subscriptions.length" description="创建第一个订阅，将节点交给 Clash 自动管理"><el-button type="primary" :disabled="!accounts.length && !sources.length" @click="newSub()">创建订阅</el-button></el-empty><div class="subscription-grid"><article v-for="s in subscriptions" :key="s.id" class="panel sub-card"><div class="card-top"><div class="account-avatar"><el-icon><Link /></el-icon></div><div class="grow"><h2>{{ s.name }}</h2><p class="muted">{{ s.account_ids.length }} 个账号 · {{ s.source_ids.length }} 个订阅源 · {{ s.regions.length ? s.regions.map(regionText).join(' / ') : '全部入口' }}{{ s.exclude_failed ? ' · 去除不可用' : '' }}{{ s.dedupe ? ' · 已去重' : '' }}{{ s.max_latency ? ' · 延迟 ≤ ' + s.max_latency + ' ms' : '' }}{{ s.max_per_country ? ' · 每国 ≤ ' + s.max_per_country + ' 个' : '' }}</p></div><el-tag :type="s.enabled ? 'success' : 'info'">{{ s.enabled ? '已启用' : '已停用' }}</el-tag></div><div class="source-tags"><el-tag v-for="id in s.account_ids" :key="id" type="info" effect="plain">{{ accounts.find(a => a.id === id)?.name || '已删除账号' }}</el-tag><el-tag v-for="id in visibleSources(s)" :key="id" type="success" effect="plain">{{ sources.find(x => x.id === id)?.name || '已删除订阅源' }}</el-tag><el-button v-if="s.source_ids.length" class="tag-more" text size="small" @click="toggleSubSources(s.id)">{{ expandedSubs.includes(s.id) ? '收起来源' : `查看 ${s.source_ids.length} 个来源` }}</el-button></div><label class="field-label">Clash 完整配置</label><div class="subscription-url"><span>{{ settings.public_base_url }}/sub/••••••••/clash.yaml</span><el-button text type="primary" @click="copy(s.clash_url)">复制地址</el-button></div><div class="card-actions"><el-button type="primary" plain :icon="Connection" @click="openDetail(s)">节点详情</el-button><el-button :icon="Refresh" :loading="busy" :disabled="!s.enabled" @click="refreshSub(s)">刷新订阅</el-button><el-button :icon="Download" :disabled="!s.enabled" @click="showConfig(s)">预览 / 下载</el-button><el-button text @click="editSub(s)">编辑</el-button><el-button text @click="rotate(s)">轮换密钥</el-button><el-button text type="danger" @click="removeSub(s)">删除</el-button></div></article></div><div class="note">在 Clash 中新建远程订阅并粘贴地址，建议客户端每 30–60 分钟更新。订阅地址具有访问权限，请妥善保存。</div></template>

        <section v-if="active === 'jobs'" class="panel"><el-table :data="pagedJobs" max-height="100vh - 430px" empty-text="暂无任务记录"><el-table-column label="任务" width="130"><template #default="{ row }">{{ jobKind[row.kind] || row.kind }}</template></el-table-column><el-table-column label="状态" width="125"><template #default="{ row }"><el-tag :type="row.status === 'completed' ? 'success' : ['partial', 'interrupted'].includes(row.status) ? 'warning' : 'info'">{{ stateLabel[row.status] || row.status }}</el-tag></template></el-table-column><el-table-column label="进度" min-width="180"><template #default="{ row }"><el-progress :percentage="Math.round(row.completed / Math.max(1, row.total) * 100)" /><small>{{ row.completed }} / {{ row.total }}</small></template></el-table-column><el-table-column label="创建时间" width="180"><template #default="{ row }">{{ time(row.created_at) }}</template></el-table-column><el-table-column label="详情" min-width="200"><template #default="{ row }"><span class="muted">{{ row.errors.slice(0, 3).join('；') || '—' }}{{ row.errors.length > 3 ? ` 等 ${row.errors.length} 项` : '' }}</span></template></el-table-column></el-table><div class="table-footer"><span>最近 7 天 · {{ jobs.length }} 条任务</span><el-pagination v-model:current-page="jobPage" v-model:page-size="jobPageSize" :page-sizes="[20, 50, 100]" :pager-count="5" :total="jobs.length" size="small" background layout="total, sizes, prev, pager, next" /></div></section>

        <section v-if="active === 'settings'" class="panel settings-panel"><el-form label-position="top" @submit.prevent="saveSettings"><div class="settings-block"><h2>获取与测速</h2><p class="muted">自动刷新只查询当前会话，不能强制上游签发新节点。</p><div class="settings-grid"><el-form-item label="自动刷新间隔（分钟）"><el-input-number v-model="settings.refresh_minutes" :min="5" :max="30" class="full" /><small class="field-hint">5–30 分钟</small></el-form-item><el-form-item label="定时测速间隔（分钟）"><el-input-number v-model="settings.probe_minutes" :min="5" :max="60" class="full" /><small class="field-hint">仅对开启「去除不可用」「最大延迟」「每国保留节点数」的订阅生效</small></el-form-item><el-form-item label="单节点测速超时（毫秒）"><el-input-number v-model="settings.probe_timeout_ms" :min="1000" :max="10000" :step="500" class="full" /><small class="field-hint">超时判为不可用；调大减少误判，但整轮更慢</small></el-form-item></div><div class="probe-engine" :class="{ warn: !settings.mihomo }"><span class="dot" />{{ settings.mihomo ? '已启用 mihomo 内核测速：每个节点按自身配置真实访问 204 检测地址。' : '未找到 mihomo 内核，当前退回端口探测，结果仅代表端口可达。' }}</div><div class="settings-actions"><el-button type="primary" native-type="submit" :loading="busy">保存设置</el-button></div></div></el-form><div class="settings-block"><h2>部署信息</h2><dl class="info-list"><div><dt>对外访问地址</dt><dd><code>{{ settings.public_base_url }}</code><small class="field-hint">通过环境变量 PUBLIC_BASE_URL 配置；远程访问时请填写实际域名。</small></dd></div><div><dt>兼容插件版本</dt><dd><code>{{ settings.extension_version }}</code></dd></div><div><dt>API 文档</dt><dd><a href="/api/docs" target="_blank" rel="noreferrer" class="text-link">查看 API 文档 →</a></dd></div></dl><div class="note">数据库及加密密钥需要一起备份。管理员密码与 API 密钥通过 .env 配置，修改后重启服务。</div></div></section>
      </main><footer class="page-footer">GHelper Clash Web <span>连接由你掌控</span></footer>
    </div>
  </div>

  <el-dialog v-model="importVisible" title="添加账号" width="580px" @closed="importText = ''"><p class="muted">每行一个 token，也可填写「名称,token」或粘贴导出 JSON。</p><el-input v-model="importText" type="textarea" :rows="8" placeholder="账号 A,32 位 token&#10;账号 B,32 位 token" aria-label="批量 token" autocomplete="off" /><label class="file-picker">导入本地 JSON <input type="file" accept=".json,application/json" @change="readImport" /></label><template #footer><el-button @click="importVisible = false">取消</el-button><el-button type="primary" :loading="busy" @click="importAccounts">导入账号</el-button></template></el-dialog>
  <el-dialog v-model="accountVisible" title="编辑账号" width="500px" @closed="editingAccount.token = ''"><el-form label-position="top"><el-form-item label="账号名称"><el-input v-model="editingAccount.name" maxlength="80" /></el-form-item><el-form-item label="替换 token（留空保持原值）"><el-input v-model="editingAccount.token" type="password" show-password autocomplete="off" /></el-form-item><el-form-item label="参与刷新和订阅"><el-switch v-model="editingAccount.enabled" /></el-form-item></el-form><template #footer><el-button @click="accountVisible = false">取消</el-button><el-button type="primary" :loading="busy" @click="saveAccount">保存</el-button></template></el-dialog>
  <el-dialog v-model="subVisible" :title="subForm.id ? '编辑订阅' : '创建订阅'" width="720px" top="6vh" class="sub-dialog"><el-form label-position="top" class="sub-form"><div class="sub-grid"><el-form-item label="订阅名称"><el-input v-model="subForm.name" maxlength="80" /></el-form-item><el-form-item label="启用订阅"><el-switch v-model="subForm.enabled" /></el-form-item></div><el-form-item label="来源账号"><el-select v-model="subForm.account_ids" multiple collapse-tags collapse-tags-tooltip :max-collapse-tags="4" placeholder="选择一个或多个 GHelper 账号" class="full"><el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}${a.enabled ? '' : '（已停用）'}`" /></el-select></el-form-item><el-form-item label="订阅源（与账号至少选一个）"><el-select v-model="subForm.source_ids" multiple collapse-tags collapse-tags-tooltip :max-collapse-tags="4" placeholder="选择一个或多个外部订阅" class="full"><el-option v-for="s in sources" :key="s.id" :value="s.id" :label="`${s.name}${s.enabled ? '' : '（已停用）'}`" /></el-select></el-form-item><el-form-item label="入口筛选（留空包含全部）"><el-select v-model="subForm.regions" multiple filterable collapse-tags collapse-tags-tooltip :max-collapse-tags="6" class="full"><el-option v-for="r in regions" :key="r" :value="r" :label="regionText(r)" /></el-select></el-form-item><div class="sub-grid"><el-form-item><template #label><span class="help-label">去除不可用节点<el-tooltip content="生成时现场探测：用 mihomo 内核按节点自身配置真实访问 204 检测地址，超时或连接失败的节点不写入订阅。" placement="top" :show-after="200"><el-icon><QuestionFilled /></el-icon></el-tooltip></span></template><el-switch v-model="subForm.exclude_failed" /></el-form-item><el-form-item><template #label><span class="help-label">去除重复节点<el-tooltip content="按协议、服务器、端口与凭据合并，保留先出现的那个。" placement="top" :show-after="200"><el-icon><QuestionFilled /></el-icon></el-tooltip></span></template><el-switch v-model="subForm.dedupe" /></el-form-item><el-form-item><template #label><span class="help-label">最大延迟（毫秒）<el-tooltip content="留空不限制。开启后会定时测速，延迟超过上限的节点不写入订阅；未测出延迟的节点保留。" placement="top" :show-after="200"><el-icon><QuestionFilled /></el-icon></el-tooltip></span></template><el-input-number v-model="subForm.max_latency" :min="1" :max="10000" :step="50" placeholder="不限制" class="full" /></el-form-item><el-form-item><template #label><span class="help-label">每国保留节点数<el-tooltip placement="top" :show-after="200" popper-class="help-tip"><template #content>留空不限制。按国家分组，只保留延迟最低的若干个节点；同一国家的多个入口（如洛杉矶、圣何塞）合并计数。<br />开启后会定时测速，没测出延迟的节点不占名额（测出结论后下一轮才参与排序），识别不出国家的节点归入「未知」单独计数。</template><el-icon><QuestionFilled /></el-icon></el-tooltip></span></template><el-input-number v-model="subForm.max_per_country" :min="1" :max="500" placeholder="不限制" class="full" /></el-form-item></div></el-form><template #footer><el-button @click="subVisible = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="(!subForm.account_ids.length && !subForm.source_ids.length) || !subForm.name.trim()" @click="saveSub">保存订阅</el-button></template></el-dialog>
  <el-dialog v-model="sourceVisible" :title="sourceForm.id ? '编辑订阅源' : '批量添加订阅源'" width="620px" @closed="sourceForm.url = ''; sourceForm.text = ''"><el-form v-if="sourceForm.id" label-position="top"><el-form-item label="名称"><el-input v-model="sourceForm.name" maxlength="80" /></el-form-item><el-form-item label="订阅地址（留空保持原值）"><el-input v-model="sourceForm.url" type="password" show-password autocomplete="off" placeholder="https://example.com/api/v1/client/subscribe?token=..." /></el-form-item><el-form-item label="启用"><el-switch v-model="sourceForm.enabled" /></el-form-item></el-form><el-form v-else label-position="top"><el-form-item label="订阅地址（每行一个，可写「名称,地址」）"><el-input v-model="sourceForm.text" type="textarea" :rows="9" placeholder="机场 A,https://example.com/api/v1/client/subscribe?token=...&#10;https://example.com/clash.yaml" /></el-form-item></el-form><p class="muted">支持 Clash YAML 与 base64 / 明文链接订阅（ss、vmess、trojan、vless、hysteria2）。未写名称时使用域名。地址加密保存，保存后立即拉取。</p><template #footer><el-button @click="sourceVisible = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="sourceForm.id ? !sourceForm.name.trim() : !sourceForm.text.trim()" @click="saveSource">保存</el-button></template></el-dialog>
  <el-dialog v-model="fileVisible" title="导入订阅文件" width="560px" @closed="fileForm.content = ''"><el-form label-position="top"><el-form-item label="订阅文件"><label class="file-picker">{{ fileForm.filename || '选择文件（Clash YAML 或链接列表）' }}<input type="file" accept=".yaml,.yml,.txt,.conf" @change="pickSourceFile" /></label></el-form-item><el-form-item label="名称"><el-input v-model="fileForm.name" maxlength="80" placeholder="默认使用文件名" /></el-form-item></el-form><p class="muted">适合订阅地址无法从服务端访问、或只有导出文件的情况。支持 Clash YAML（读取其中的 proxies）与 base64 / 明文链接列表；文件内容只保存解析后的节点，不会自动更新。</p><template #footer><el-button @click="fileVisible = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!fileForm.content || !fileForm.name.trim()" @click="saveSourceFile">导入</el-button></template></el-dialog>
  <el-dialog v-model="previewVisible" title="配置预览" width="850px" @closed="preview = ''"><pre class="config-preview">{{ preview }}</pre><template #footer><el-button @click="copy(preview)">复制内容</el-button><el-button type="primary" :icon="Download" @click="downloadPreview">下载 YAML</el-button></template></el-dialog>
  <el-dialog v-model="detailVisible" :title="`节点详情 · ${detailSub?.name || ''}`" width="1100px" top="5vh" @closed="closeDetail">
    <div class="detail-stats"><div><span>候选节点</span><strong>{{ detailStats.total }}</strong></div><div><span>写入订阅</span><strong class="latency-good">{{ detailStats.included }}</strong></div><div><span>可用</span><strong>{{ detailStats.ok }}</strong></div><div><span>失败</span><strong class="latency-bad">{{ detailStats.failed }}</strong></div><div><span>未测速</span><strong>{{ detailStats.untested }}</strong></div><div><span>延迟中位数</span><strong>{{ detailStats.median === null ? '—' : `${detailStats.median} ms` }}</strong></div></div>
    <p v-if="detailSub" class="muted">当前条件：{{ detailSub.exclude_failed ? '去除不可用' : '保留不可用' }}{{ detailSub.dedupe ? ' · 去重' : '' }}{{ detailSub.max_latency ? ` · 延迟 ≤ ${detailSub.max_latency} ms` : '' }}{{ detailSub.max_per_country ? ` · 每国 ≤ ${detailSub.max_per_country} 个` : '' }}。灰色行不会写入订阅，原因见最后一列。</p>
    <div v-if="detailJob" class="running-banner" role="status"><el-icon class="is-loading"><Refresh /></el-icon><span>正在测速</span><el-progress :percentage="Math.round(detailJob.completed / Math.max(1, detailJob.total) * 100)" :stroke-width="6" /><span>{{ detailJob.completed }} / {{ detailJob.total }}</span></div>
    <div class="filters"><el-input v-model="detailSearch" placeholder="搜索名称、地址或来源" :prefix-icon="Search" clearable aria-label="搜索订阅节点" /><el-select v-model="detailCountry" placeholder="全部国家" clearable filterable aria-label="筛选国家"><el-option v-for="[code, text] in detailCountries" :key="code" :label="text" :value="code" /></el-select><el-select v-model="detailOrigin" placeholder="全部来源" clearable filterable aria-label="筛选来源"><el-option v-for="o in detailOrigins" :key="o" :label="o" :value="o" /></el-select><el-select v-model="detailStatus" placeholder="全部状态" clearable aria-label="筛选状态"><el-option label="写入订阅" value="included" /><el-option label="被过滤" value="excluded" /><el-option label="可用" value="ok" /><el-option label="失败" value="failed" /><el-option label="未测速" value="untested" /></el-select><el-button :loading="detailLoading" @click="action(loadDetail)">刷新列表</el-button><el-button :icon="Refresh" :loading="busy" :disabled="!!detailJobId" @click="refreshSub(detailSub!)">刷新订阅</el-button><el-button type="primary" :loading="busy" :disabled="!detailFiltered.length || !!detailJobId" @click="probeDetail">{{ detailSelected.length ? `测速选中 (${detailSelected.length})` : detailFiltered.length < detailNodes.length ? `测速筛选结果 (${detailFiltered.length})` : '全部测速' }}</el-button></div>
    <el-table :data="detailPaged" row-key="key" max-height="55vh" :default-sort="{ prop: 'latency', order: 'ascending' }" @sort-change="sortDetail" :row-class-name="({ row }: { row: SubNode }) => row.included ? '' : 'row-excluded'" @selection-change="(v: SubNode[]) => detailSelected = v" empty-text="没有符合条件的节点" v-loading="detailLoading">
      <el-table-column type="selection" width="42" reserve-selection />
      <el-table-column label="节点名称" prop="name" min-width="200" sortable="custom" show-overflow-tooltip />
      <el-table-column label="来源" prop="origin" min-width="150" sortable="custom" show-overflow-tooltip />
      <el-table-column label="国家" prop="country_label" width="115" sortable="custom" />
      <el-table-column label="协议 / 地址" min-width="190"><template #default="{ row }"><code class="server">{{ row.server }}:{{ row.port }}</code><div class="muted">{{ row.type }}</div></template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.reachable === true ? 'success' : row.reachable === false ? 'danger' : 'info'">{{ row.reachable === true ? '可用' : row.reachable === false ? '失败' : row.method === 'timeout' ? '超时' : '未测速' }}</el-tag></template></el-table-column>
      <el-table-column label="延迟" prop="latency" width="105" sortable="custom"><template #default="{ row }"><span :class="latencyClass(row)">{{ row.latency === null ? '—' : `${row.latency} ms` }}</span><div v-if="row.method === 'tcp'" class="muted">仅端口</div></template></el-table-column>
      <el-table-column label="测速时间" prop="tested_at" width="165" sortable="custom"><template #default="{ row }">{{ time(row.tested_at) }}</template></el-table-column>
      <el-table-column label="订阅中" width="150"><template #default="{ row }"><el-tag v-if="row.included" type="success" effect="plain">写入</el-tag><span v-else class="muted">{{ row.reason }}</span></template></el-table-column>
    </el-table>
    <div class="table-footer"><span>{{ detailFiltered.length }} 个节点 · 经 mihomo 内核按节点配置访问 204 地址计时，测不通的节点不写入订阅；没有内核时退回端口探测（标注「仅端口」）；均不代表带宽。</span><el-pagination v-model:current-page="detailPage" v-model:page-size="detailPageSize" :page-sizes="[50, 100, 200]" :pager-count="5" :total="detailFiltered.length" size="small" background layout="total, sizes, prev, pager, next" /></div>
  </el-dialog>
</template>
