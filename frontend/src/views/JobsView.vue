<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppDialog from '../components/AppDialog.vue'
import FilterBar from '../components/FilterBar.vue'
import type { Job } from '../types'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import { action, jobKind, jobs, load, running, stateLabel } from '../store'
import { api } from '../api'
import { time } from '../utils'

const page = ref(1)
const pageSize = ref(20)
const kind = ref('')
const status = ref('')
const detail = ref<Job | null>(null)
const detailVisible = ref(false)
const filtered = computed(() => jobs.value.filter(j => (!kind.value || j.kind === kind.value) && (!status.value || j.status === status.value)))
const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([kind, status], () => { page.value = 1 })
function openDetail(job: Job) { detail.value = job; detailVisible.value = true }
watch(jobs, rows => { if (detail.value) detail.value = rows.find(r => r.id === detail.value!.id) || detail.value })
async function cancel(job: Job) { await action(async () => { await api('/jobs/' + job.id + '/cancel', 'POST'); await load() }) }

watch([pageSize, () => filtered.value.length], () => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
})
</script>

<template>
  <PageHeading title="任务记录" subtitle="最近 7 天的手动刷新、测速与订阅源检测任务。" />
  <RunningBanner :running="running.length > 0" />

  <section class="panel">
    <FilterBar>
      <el-select v-model="kind" clearable placeholder="全部任务" aria-label="任务类型"><el-option v-for="(label, value) in jobKind" :key="value" :label="label" :value="value" /></el-select>
      <el-select v-model="status" clearable placeholder="全部状态" aria-label="任务状态"><el-option v-for="value in ['pending', 'running', 'completed', 'partial', 'interrupted', 'done', 'cancelled', 'failed']" :key="value" :label="stateLabel[value]" :value="value" /></el-select>
      <template #actions><el-button v-if="kind || status" text @click="kind = ''; status = ''">清空筛选</el-button></template>
    </FilterBar>
    <el-table :data="paged" max-height="calc(100dvh - 320px)" :empty-text="jobs.length ? '没有符合条件的任务' : '暂无任务记录'">
      <el-table-column label="任务" width="130">
        <template #default="{ row }">{{ jobKind[row.kind] || row.kind }}</template>
      </el-table-column>
      <el-table-column label="状态" width="125">
        <template #default="{ row }">
          <el-tag :type="row.status === 'completed' ? 'success' : ['partial', 'interrupted'].includes(row.status) ? 'warning' : 'info'">
            {{ stateLabel[row.status] || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" min-width="180">
        <template #default="{ row }">
          <el-progress :percentage="Math.round(row.completed / Math.max(1, row.total) * 100)" />
          <small>{{ row.completed }} / {{ row.total }}</small>
        </template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ time(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="详情" min-width="200">
        <template #default="{ row }">
          <span class="muted">{{ row.errors.slice(0, 3).join('；') || '—' }}{{ row.errors.length > 3 ? ` 等 ${row.errors.length} 项` : '' }}</span>
          <el-button v-if="row.errors.length || row.result" text type="primary" @click="openDetail(row)">查看完整详情</el-button>
          <el-button v-if="row.kind === 'chain_diagnostic' && ['pending', 'running'].includes(row.status)" text @click="cancel(row)">取消</el-button>
        </template>
      </el-table-column>
    </el-table>
    <div class="table-footer">
      <span>最近 7 天 · {{ filtered.length }} 条任务</span>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" :pager-count="5" :total="filtered.length" size="small" background layout="total, sizes, prev, pager, next" />
    </div>
  </section>
  <AppDialog v-model="detailVisible" title="任务详情" :form="false" @closed="detail = null">
    <template v-if="detail"><h3>{{ jobKind[detail.kind] || detail.kind }} · {{ time(detail.created_at) }}</h3><p class="muted">共 {{ detail.errors.length }} 项错误</p><ol class="error-list"><li v-for="(message, index) in detail.errors" :key="index">{{ message }}</li></ol></template>
    <template v-if="detail?.result"><p v-if="detail.result.path">检测路径：{{ detail.result.path.hops.map(h => h.name).join(' → ') }}</p><p v-if="detail.result.exit_ip">出口 IP：{{ detail.result.exit_ip }}</p><p v-for="(step, i) in detail.result.steps" :key="i">{{ step.label }}：{{ step.state === 'passed' ? '通过' : step.state === 'failed' ? '失败' : '未确定' }} {{ step.latency != null ? step.latency + ' ms' : '' }} {{ step.error }}</p></template>
  </AppDialog>
</template>
