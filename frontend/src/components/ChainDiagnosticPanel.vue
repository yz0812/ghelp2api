<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { time } from '../utils'

/** 服务器侧完整路径诊断。草稿模式在打开后自动开始一次检测。 */
const props = defineProps<{ chainId?: string; draft?: Record<string, any>; active: boolean }>()
const emit = defineEmits<{ completed: [] }>()
const job = ref<any>(null), pending = ref(false), error = ref(''), busy = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
const running = computed(() => pending.value || ['pending', 'running'].includes(job.value?.status))
const result = computed(() => job.value?.result)
const stepTone: Record<string, string> = { passed: 'ok', failed: 'error' }
const stepLabel: Record<string, string> = { passed: '通过', failed: '失败' }

async function poll(token: number) {
  if (token !== generation || !job.value?.id) return
  try {
    const value = await api('/jobs/' + job.value.id)
    if (token !== generation) return
    job.value = value
    if (['pending', 'running'].includes(value.status)) timer = setTimeout(() => poll(token), 1000)
    else emit('completed')
  } catch (e) { if (token === generation) error.value = (e as Error).message }
}
async function start() {
  error.value = ''; pending.value = true
  const token = generation
  try {
    const response = await api('/chains/diagnostics', 'POST', props.draft ? { draft: props.draft } : { chain_id: props.chainId })
    if (token !== generation) return
    job.value = { id: response.job_id, status: 'pending', result: { steps: [] } }
    await poll(token)
  } catch (e) { if (token === generation) error.value = (e as Error).message } finally { pending.value = false }
}
async function cancel() {
  if (!job.value?.id) return
  busy.value = true
  try { await api('/jobs/' + job.value.id + '/cancel', 'POST'); await poll(generation) }
  catch (e) { ElMessage.error((e as Error).message) } finally { busy.value = false }
}
watch(() => [props.active, props.chainId, props.draft] as const, ([active]) => {
  ++generation; clearTimeout(timer)
  job.value = null; error.value = ''
  if (active && props.draft) start()
}, { immediate: true })
onUnmounted(() => { ++generation; clearTimeout(timer) })
</script>

<template>
  <div class="diagnostic">
    <div class="diagnostic-head">
      <p class="muted">经服务器逐段检测完整路径与出口 IP，不会改变正在运行的选路。累计延迟不是相邻节点间的延迟。</p>
      <div class="toolbar-actions">
        <el-button type="primary" :loading="running" @click="start">{{ draft ? '重新检测草稿' : job ? '重新诊断' : '开始完整诊断' }}</el-button>
        <el-button v-if="running && job?.id" :loading="busy" @click="cancel">取消检测</el-button>
      </div>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb" />
    <p v-if="running" role="status" class="muted">正在检测，最长 120 秒；关闭面板后任务仍会继续，可在任务记录查看。</p>
    <template v-if="result">
      <div v-if="result.tested_at" :class="['diagnostic-summary', result.reachable === true ? 'is-ok' : result.reachable === false ? 'is-error' : '']">
        <strong>{{ result.reachable === true ? '链路可用' : result.reachable === false ? '链路不可用' : '结果未确定' }}</strong>
        <span v-if="result.latency != null">{{ result.latency }} ms</span>
        <span v-if="result.exit_ip">出口 IP <code>{{ result.exit_ip }}</code></span>
        <small>{{ time(result.tested_at) }}</small>
      </div>
      <el-alert v-if="result.error" :title="result.error" type="warning" :closable="false" class="mb" />
      <p v-if="result.path" class="muted">本次路径：{{ result.path.hops.map((h: any) => h.name).join(' → ') }}</p>
      <ol v-if="result.steps?.length" class="diagnostic-steps">
        <li v-for="(step, index) in result.steps" :key="index" :class="'is-' + (stepTone[step.state] || 'idle')">
          <span class="step-dot" aria-hidden="true" />
          <div class="grow"><strong>{{ step.label }}</strong><p v-if="step.value || step.error">{{ step.value || step.error }}</p></div>
          <span v-if="step.latency != null" class="step-latency">{{ step.latency }} ms</span>
          <span class="step-state">{{ stepLabel[step.state] || '未确定' }}</span>
        </li>
      </ol>
      <p v-for="item in result.path_errors || []" :key="item.path_index" class="danger-text">路径 {{ item.path_index + 1 }}：{{ item.error }}</p>
    </template>
    <p v-else-if="!running && !error" class="muted">诊断包含完整路径、累计前缀检测和出口 IP。客户端原生订阅的实际路径请在客户端查看。</p>
  </div>
</template>

<style scoped>
.diagnostic-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
.diagnostic-head p { flex: 1 1 260px; margin: 0; }
.diagnostic-summary { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px; padding: 14px 16px; margin-bottom: 12px; border-radius: 10px; background: var(--surface-soft); border: 1px solid var(--line); font-size: 13px; overflow-wrap: anywhere; }
.diagnostic-summary strong { font-size: 15px; }
.diagnostic-summary small { color: var(--muted); margin-left: auto; }
.diagnostic-summary.is-ok { background: var(--success-soft); }
.diagnostic-summary.is-ok strong { color: var(--success); }
.diagnostic-summary.is-error { background: var(--danger-soft); }
.diagnostic-summary.is-error strong { color: var(--danger); }
.diagnostic-steps { margin: 0; padding: 0; list-style: none; }
.diagnostic-steps li { position: relative; display: flex; align-items: flex-start; gap: 12px; padding: 10px 0 10px 4px; font-size: 13px; overflow-wrap: anywhere; }
.diagnostic-steps li + li::before { content: ""; position: absolute; left: 8px; top: -8px; height: 20px; border-left: 2px solid var(--line); }
.diagnostic-steps p { margin: 2px 0 0; color: var(--muted); font-size: 12px; }
.step-dot { flex: none; width: 10px; height: 10px; margin-top: 5px; border-radius: 50%; background: var(--subtle); box-shadow: 0 0 0 3px var(--surface-hover); }
.is-ok .step-dot { background: var(--success); box-shadow: 0 0 0 3px var(--success-soft); }
.is-error .step-dot { background: var(--danger); box-shadow: 0 0 0 3px var(--danger-soft); }
.step-latency { flex: none; font-variant-numeric: tabular-nums; }
.step-state { flex: none; min-width: 40px; text-align: right; color: var(--muted); font-size: 12px; }
.is-ok .step-state { color: var(--success); }
.is-error .step-state { color: var(--danger); }
</style>
