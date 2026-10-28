<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../api'
import { action, busy, jobs, load, schedulePoll } from '../store'
import { latencyClass, time } from '../utils'
import type { SubNode, Subscription } from '../types'

const props = defineProps<{ modelValue: boolean, subscription: Subscription | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })

const nodeList = ref<SubNode[]>([])
const dialogLoading = ref(false)
const detailJobId = ref('')
const search = ref('')
const country = ref('')
const origin = ref('')
const status = ref('')
const selected = ref<SubNode[]>([])
const page = ref(1)
const pageSize = ref(50)
const sort = reactive({ prop: 'latency', order: 'ascending' as 'ascending' | 'descending' | null })
let timer: ReturnType<typeof setTimeout> | undefined

const countries = computed(() => [...new Map(nodeList.value.map(n => [n.country, n.country_label])).entries()].sort((a, b) => a[1].localeCompare(b[1], 'zh-CN')))
const origins = computed(() => [...new Set(nodeList.value.map(n => n.origin))].sort((a, b) => a.localeCompare(b, 'zh-CN')))

function sortValue(n: SubNode, prop: string): number | string | null {
  if (prop === 'latency') return n.latency
  if (prop === 'tested_at') return n.tested_at
  if (prop === 'country_label') return n.country_label
  if (prop === 'origin') return n.origin
  return n.name
}

function compare(a: SubNode, b: SubNode) {
  const x = sortValue(a, sort.prop), y = sortValue(b, sort.prop)
  if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1 // 未测速的始终排在最后
  const result = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'zh-CN')
  return sort.order === 'descending' ? -result : result
}

// 先在整份列表上过滤排序，再分页——el-table 自带排序只排当前页。
const filtered = computed(() => nodeList.value.filter(n =>
  (!country.value || n.country === country.value) &&
  (!origin.value || n.origin === origin.value) &&
  (!search.value || `${n.name} ${n.server} ${n.group}`.toLowerCase().includes(search.value.toLowerCase())) &&
  (!status.value || (status.value === 'included' ? n.included
    : status.value === 'excluded' ? !n.included
    : status.value === 'ok' ? n.reachable === true
    : status.value === 'failed' ? n.reachable === false
    : n.reachable === null)))
  .sort((a, b) => sort.order ? compare(a, b) : 0))

const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))

const stats = computed(() => {
  const tested = nodeList.value.filter(n => n.latency !== null).map(n => n.latency!).sort((a, b) => a - b)
  return {
    total: nodeList.value.length,
    included: nodeList.value.filter(n => n.included).length,
    ok: nodeList.value.filter(n => n.reachable === true).length,
    failed: nodeList.value.filter(n => n.reachable === false).length,
    untested: nodeList.value.filter(n => n.reachable === null).length,
    median: tested.length ? tested[Math.floor(tested.length / 2)] : null
  }
})

const job = computed(() => jobs.value.find(j => j.id === detailJobId.value))

watch([search, country, origin, status, pageSize, () => sort.prop, () => sort.order], () => { page.value = 1 })

function onSort({ prop, order }: { prop: string, order: 'ascending' | 'descending' | null }) {
  sort.prop = prop
  sort.order = order
}

async function loadNodes() {
  if (!props.subscription) return
  const result = await api(`/subscriptions/${props.subscription.id}/nodes`)
  nodeList.value = result.nodes
  detailJobId.value = result.job_id || ''
  clearTimeout(timer)
  if (detailJobId.value && visible.value) timer = setTimeout(() => loadNodes().catch(e => ElMessage.error(e.message)), 2000)
}

watch(visible, async open => {
  clearTimeout(timer)
  if (!open) { nodeList.value = []; detailJobId.value = ''; return }
  Object.assign(sort, { prop: 'latency', order: 'ascending' })
  search.value = ''; country.value = ''; origin.value = ''; status.value = ''; selected.value = []; page.value = 1
  dialogLoading.value = true
  try { await loadNodes() } catch (e: any) { ElMessage.error(e.message) } finally { dialogLoading.value = false }
})

async function refreshSubscription() {
  const sub = props.subscription
  if (!sub) return
  await action(async () => {
    ElMessage.info('正在重新拉取账号与订阅源…')
    const result = await api(`/subscriptions/${sub.id}/refresh`, 'POST')
    ElMessage.success(result.count ? `已重新拉取，开始测速 ${result.count} 个节点；完成后订阅按规则使用新结果` : '该订阅的测速仍在进行中')
    await load()
    schedulePoll()
    await loadNodes()
  })
}

async function probe() {
  const sub = props.subscription
  if (!sub) return
  await action(async () => {
    const keys = selected.value.length
      ? selected.value.map(n => n.key)
      : filtered.value.length < nodeList.value.length ? filtered.value.map(n => n.key) : null
    const result = await api(`/subscriptions/${sub.id}/probe`, 'POST', { keys })
    ElMessage.success(result.count ? `已开始测速 ${result.count} 个节点` : '该订阅的测速仍在进行中')
    await load()
    schedulePoll()
    await loadNodes()
  })
}

onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <el-dialog v-model="visible" :title="`节点详情 · ${subscription?.name || ''}`" width="1100px" top="5vh">
    <div class="detail-stats">
      <div><span>候选节点</span><strong>{{ stats.total }}</strong></div>
      <div><span>写入订阅</span><strong class="latency-good">{{ stats.included }}</strong></div>
      <div><span>可用</span><strong>{{ stats.ok }}</strong></div>
      <div><span>失败</span><strong class="latency-bad">{{ stats.failed }}</strong></div>
      <div><span>未测速</span><strong>{{ stats.untested }}</strong></div>
      <div><span>延迟中位数</span><strong>{{ stats.median === null ? '—' : `${stats.median} ms` }}</strong></div>
    </div>
    <p v-if="subscription" class="muted">
      当前条件：{{ subscription.exclude_failed ? '去除不可用' : '保留不可用' }}{{ subscription.dedupe ? ' · 去重' : '' }}{{ subscription.max_latency ? ` · 延迟 ≤ ${subscription.max_latency} ms` : '' }}{{ subscription.max_per_country ? ` · 每国 ≤ ${subscription.max_per_country} 个` : '' }}。灰色行不会写入订阅，原因见最后一列。
    </p>
    <div v-if="job" class="running-banner" role="status">
      <el-icon class="is-loading"><Refresh /></el-icon>
      <span>正在测速</span>
      <el-progress :percentage="Math.round(job.completed / Math.max(1, job.total) * 100)" :stroke-width="6" />
      <span>{{ job.completed }} / {{ job.total }}</span>
    </div>
    <div class="filters">
      <el-input v-model="search" placeholder="搜索名称、地址或来源" :prefix-icon="Search" clearable aria-label="搜索订阅节点" />
      <el-select v-model="country" placeholder="全部国家" clearable filterable aria-label="筛选国家">
        <el-option v-for="[code, text] in countries" :key="code" :label="text" :value="code" />
      </el-select>
      <el-select v-model="origin" placeholder="全部来源" clearable filterable aria-label="筛选来源">
        <el-option v-for="o in origins" :key="o" :label="o" :value="o" />
      </el-select>
      <el-select v-model="status" placeholder="全部状态" clearable aria-label="筛选状态">
        <el-option label="写入订阅" value="included" />
        <el-option label="被过滤" value="excluded" />
        <el-option label="可用" value="ok" />
        <el-option label="失败" value="failed" />
        <el-option label="未测速" value="untested" />
      </el-select>
      <el-button :loading="dialogLoading" @click="action(loadNodes)">刷新列表</el-button>
      <el-button :icon="Refresh" :loading="busy" :disabled="!!detailJobId" @click="refreshSubscription">刷新订阅</el-button>
      <el-button type="primary" :loading="busy" :disabled="!filtered.length || !!detailJobId" @click="probe">
        {{ selected.length ? `测速选中 (${selected.length})` : filtered.length < nodeList.length ? `测速筛选结果 (${filtered.length})` : '全部测速' }}
      </el-button>
    </div>
    <el-table
      :data="paged" row-key="key" max-height="55vh"
      :default-sort="{ prop: 'latency', order: 'ascending' }"
      :row-class-name="({ row }: { row: SubNode }) => row.included ? '' : 'row-excluded'"
      @sort-change="onSort"
      @selection-change="(v: SubNode[]) => selected = v"
      empty-text="没有符合条件的节点" v-loading="dialogLoading"
    >
      <el-table-column type="selection" width="42" reserve-selection />
      <el-table-column label="节点名称" prop="name" min-width="200" sortable="custom" show-overflow-tooltip />
      <el-table-column label="来源" prop="origin" min-width="150" sortable="custom" show-overflow-tooltip />
      <el-table-column label="国家" prop="country_label" width="115" sortable="custom" />
      <el-table-column label="协议 / 地址" min-width="190">
        <template #default="{ row }">
          <code class="server">{{ row.server }}:{{ row.port }}</code>
          <div class="muted">{{ row.type }}</div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="row.reachable === true ? 'success' : row.reachable === false ? 'danger' : 'info'">
            {{ row.reachable === true ? '可用' : row.reachable === false ? '失败' : row.method === 'timeout' ? '超时' : '未测速' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="延迟" prop="latency" width="105" sortable="custom">
        <template #default="{ row }">
          <span :class="latencyClass(row)">{{ row.latency === null ? '—' : `${row.latency} ms` }}</span>
          <div v-if="row.method === 'tcp'" class="muted">仅端口</div>
        </template>
      </el-table-column>
      <el-table-column label="测速时间" prop="tested_at" width="165" sortable="custom">
        <template #default="{ row }">{{ time(row.tested_at) }}</template>
      </el-table-column>
      <el-table-column label="订阅中" width="150">
        <template #default="{ row }">
          <el-tag v-if="row.included" type="success" effect="plain">写入</el-tag>
          <span v-else class="muted">{{ row.reason }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div class="table-footer">
      <span>{{ filtered.length }} 个节点 · 经 mihomo 内核按节点配置访问 204 地址计时，测不通的节点不写入订阅；没有内核时退回端口探测（标注「仅端口」）；均不代表带宽。</span>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[50, 100, 200]" :pager-count="5" :total="filtered.length" size="small" background layout="total, sizes, prev, pager, next" />
    </div>
  </el-dialog>
</template>
