<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { api } from '../api'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import { accounts, action, busy, loading, load, nodes, regionText, regions, running, schedulePoll } from '../store'
import { time } from '../utils'
import type { Node } from '../types'

const search = ref('')
const accountFilter = ref('')
const regionFilter = ref('')
const statusFilter = ref('')
const selectedNodes = ref<Node[]>([])
const page = ref(1)
const pageSize = ref(20)

const filtered = computed(() => nodes.value.filter(n =>
  (!accountFilter.value || n.account_id === accountFilter.value) &&
  (!regionFilter.value || n.region === regionFilter.value) &&
  (!search.value || `${n.server} ${n.account_name} ${n.region} ${n.region_label}`.toLowerCase().includes(search.value.toLowerCase())) &&
  (!statusFilter.value || (statusFilter.value === 'ok' ? n.reachable === true : statusFilter.value === 'failed' ? n.reachable === false : n.reachable === null))))

const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))

watch([search, accountFilter, regionFilter, statusFilter], () => { page.value = 1 })
watch([pageSize, () => filtered.value.length], () => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
})

async function probe() {
  await action(async () => {
    const list = selectedNodes.value.length ? selectedNodes.value : filtered.value
    await api('/probes', 'POST', { node_ids: list.map(n => n.id) })
    await load()
    schedulePoll()
    ElMessage.success('测速任务已提交')
  })
}
</script>

<template>
  <PageHeading title="节点列表" subtitle="查看入口与连通性，找到适合当前网络的节点。" />
  <RunningBanner :running="running.length > 0" />

  <section class="panel">
    <div class="filters">
      <el-input v-model="search" placeholder="搜索节点或账号" :prefix-icon="Search" clearable aria-label="搜索节点" />
      <el-select v-model="accountFilter" placeholder="全部账号" clearable aria-label="筛选账号">
        <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
      </el-select>
      <el-select v-model="regionFilter" placeholder="全部入口" clearable aria-label="筛选入口">
        <el-option v-for="r in regions" :key="r" :label="regionText(r)" :value="r" />
      </el-select>
      <el-select v-model="statusFilter" placeholder="全部状态" clearable aria-label="筛选状态">
        <el-option label="已测通" value="ok" />
        <el-option label="连接失败" value="failed" />
        <el-option label="未测速" value="untested" />
      </el-select>
      <el-button type="primary" :loading="busy" :disabled="!filtered.length || running.some(j => j.kind === 'probe')" @click="probe">
        {{ selectedNodes.length ? `测速选中 (${selectedNodes.length})` : '测速当前列表' }}
      </el-button>
    </div>
    <el-table :data="paged" row-key="id" max-height="100vh - 470px" @selection-change="(v: Node[]) => selectedNodes = v" empty-text="暂无节点，请先添加账号并刷新" v-loading="loading">
      <el-table-column type="selection" width="42" reserve-selection />
      <el-table-column label="入口" prop="region" width="140" sortable>
        <template #default="{ row }">{{ regionText(row.region) }}</template>
      </el-table-column>
      <el-table-column label="节点地址" min-width="260">
        <template #default="{ row }">
          <code class="server">{{ row.server }}</code>
          <div class="muted">HTTPS · {{ row.port }}</div>
        </template>
      </el-table-column>
      <el-table-column label="来源账号" prop="account_name" min-width="125" />
      <el-table-column label="连接状态" width="120">
        <template #default="{ row }">
          <el-tag :type="row.stale || !row.enabled ? 'info' : row.reachable === true ? 'success' : row.reachable === false ? 'danger' : 'info'">
            {{ !row.enabled ? '已停用' : row.stale ? '快照过期' : row.reachable === true ? '已测通' : row.reachable === false ? '连接失败' : '未测速' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="延迟" prop="latency" sortable width="110">
        <template #default="{ row }">{{ row.latency === null ? '—' : `${row.latency} ms` }}</template>
      </el-table-column>
      <el-table-column label="测速时间" width="180">
        <template #default="{ row }">{{ time(row.tested_at) }}</template>
      </el-table-column>
    </el-table>
    <div class="table-footer">
      <span>{{ filtered.length }} 个节点 · 测速请求经节点代理访问 204 检测地址，不进行带宽测试。</span>
      <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :page-sizes="[20, 50, 100]" :pager-count="5" :total="filtered.length" size="small" background layout="total, sizes, prev, pager, next" />
    </div>
  </section>
</template>
