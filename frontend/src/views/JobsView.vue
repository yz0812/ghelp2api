<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import { jobKind, jobs, running, stateLabel } from '../store'
import { time } from '../utils'

const page = ref(1)
const pageSize = ref(20)
const paged = computed(() => jobs.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))

watch([pageSize, () => jobs.value.length], () => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(jobs.value.length / pageSize.value)))
})
</script>

<template>
  <PageHeading title="任务记录" subtitle="最近 7 天的手动刷新、测速与订阅源检测任务。" />
  <RunningBanner :running="running.length > 0" />

  <section class="panel">
    <el-table :data="paged" max-height="100vh - 430px" empty-text="暂无任务记录">
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
        </template>
      </el-table-column>
    </el-table>
    <div class="table-footer">
      <span>最近 7 天 · {{ jobs.length }} 条任务</span>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" :pager-count="5" :total="jobs.length" size="small" background layout="total, sizes, prev, pager, next" />
    </div>
  </section>
</template>
