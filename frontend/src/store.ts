import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, setCsrf } from './api'
import type { Account, Job, Node, Preferences, Source, Subscription } from './types'

export { jobKind, stateLabel } from './utils'

/** 全局会话与业务数据。多个页面共享同一份状态，避免来回请求。 */
export const booting = ref(true)
export const loggedIn = ref(false)
export const loading = ref(false)
export const busy = ref(false)
export const error = ref('')

export const accounts = ref<Account[]>([])
export const nodes = ref<Node[]>([])
export const sources = ref<Source[]>([])
export const subscriptions = ref<Subscription[]>([])
export const jobs = ref<Job[]>([])
export const settings = reactive<Preferences>({
  refresh_minutes: 30, probe_minutes: 30, probe_timeout_ms: 5000,
  mihomo: false, public_base_url: '', extension_version: '', version: ''
})

export const running = computed(() => jobs.value.filter(j => ['pending', 'running'].includes(j.status)))
export const healthy = computed(() => accounts.value.filter(a => a.enabled && a.status === 'ready').length)
export const usable = computed(() => nodes.value.filter(n => n.enabled && !n.stale))
export const regions = computed(() => [...new Set(nodes.value.map(n => n.region))].sort())
export const regionLabels = computed(() => Object.fromEntries(nodes.value.map(n => [n.region, n.region_label])))
export const invalidSources = computed(() => sources.value.filter(s => s.invalid))
export const checkingSources = computed(() => running.value.some(j => j.kind === 'source'))

export function regionText(code: string) { return regionLabels.value[code] || code }

let pollTimer: ReturnType<typeof setTimeout> | undefined
let bootPromise: Promise<void> | undefined

export function clearError() { error.value = '' }

/** 执行一次会修改数据的操作：统一处理忙碌状态与错误提示。 */
export async function action(fn: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try {
    await fn()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return // ElMessageBox 取消，不算错误
    error.value = e?.message || '操作失败'
    ElMessage.error(error.value)
  } finally {
    busy.value = false
  }
}

export async function load() {
  loading.value = true
  try {
    const [a, n, src, s, j, config] = await Promise.all([
      api('/accounts'), api('/nodes'), api('/sources'), api('/subscriptions'), api('/jobs'), api('/settings')
    ])
    accounts.value = a
    nodes.value = n
    sources.value = src
    subscriptions.value = s
    jobs.value = j
    Object.assign(settings, config)
  } finally {
    loading.value = false
  }
}

/** 有任务时 2 秒轮询，空闲时 15 秒。登出后自动停止。 */
export function schedulePoll() {
  clearTimeout(pollTimer)
  if (!loggedIn.value) return
  pollTimer = setTimeout(async () => {
    try { await load() } catch (e: any) { error.value = e?.message || '' }
    finally { schedulePoll() }
  }, running.value.length ? 2000 : 15000)
}

export function expire() {
  clearTimeout(pollTimer)
  setCsrf('')
  loggedIn.value = false
  accounts.value = []
  nodes.value = []
  sources.value = []
  subscriptions.value = []
  jobs.value = []
}

export function boot() {
  bootPromise ??= (async () => {
    try {
      const user = await api('/auth/me')
      setCsrf(user.csrf)
      loggedIn.value = true
      await load()
      schedulePoll()
    } catch {
      // 未登录，交给路由守卫跳转到登录页。
    } finally {
      booting.value = false
    }
  })()
  return bootPromise
}

export async function login(username: string, password: string) {
  const result = await api('/auth/login', 'POST', { username, password })
  setCsrf(result.csrf)
  loggedIn.value = true
  await load()
  schedulePoll()
}

export async function logout() {
  try { await api('/auth/logout', 'POST') } finally { expire() }
}
