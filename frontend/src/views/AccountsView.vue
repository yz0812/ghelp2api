<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Key, Plus, Refresh, Search } from '@element-plus/icons-vue'
import { api } from '../api'
import ActionMenu from '../components/ActionMenu.vue'
import FilterBar from '../components/FilterBar.vue'
import EmptyState from '../components/EmptyState.vue'
import PageHeading from '../components/PageHeading.vue'
import AccountImportDialog from '../components/AccountImportDialog.vue'
import AccountEditDialog from '../components/AccountEditDialog.vue'
import { accounts, action, busy, load, running, schedulePoll, stateLabel } from '../store'
import { bytes, time } from '../utils'
import type { Account } from '../types'

const search = ref('')
const status = ref('')
const page = ref(1)
const pageSize = 12
const filtered = computed(() => accounts.value.filter(a =>
  (!search.value || (a.name + ' ' + (a.info.email || '')).toLowerCase().includes(search.value.toLowerCase())) &&
  (!status.value || (status.value === 'disabled' ? !a.enabled : status.value === 'error' ? a.enabled && ['invalid', 'error'].includes(a.status) : a.enabled && a.status === status.value))))
const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
watch([search, status], () => { page.value = 1 })
watch(() => filtered.value.length, length => { page.value = Math.min(page.value, Math.max(1, Math.ceil(length / pageSize))) })
const router = useRouter()
const importVisible = ref(false)
const editVisible = ref(false)
const editing = ref<Account | null>(null)

async function refreshAll() {
  await action(async () => {
    await api('/accounts/all/refresh', 'POST')
    await load()
    schedulePoll()
    ElMessage.success('刷新任务已提交')
  })
}

async function refreshOne(account: Account) {
  await action(async () => {
    await api(`/accounts/${account.id}/refresh`, 'POST')
    await load()
    schedulePoll()
    ElMessage.success('刷新任务已提交')
  })
}

function edit(account: Account) {
  editing.value = account
  editVisible.value = true
}

async function remove(account: Account) {
  await action(async () => {
    await ElMessageBox.confirm(`删除「${account.name}」及其节点？订阅将移除此账号。`, '删除账号', { type: 'warning' })
    await api(`/accounts/${account.id}`, 'DELETE')
    await load()
    ElMessage.success('账号已删除')
  })
}
</script>

<template>
  <PageHeading title="账号管理" subtitle="添加多个 token，独立管理每个账号的连接状态。">
    <el-button type="primary" :icon="Plus" @click="importVisible = true">添加账号</el-button>
  </PageHeading>

  <FilterBar>
    <el-input v-model="search" :prefix-icon="Search" clearable placeholder="搜索账号名称或邮箱" aria-label="搜索账号" />
    <el-select v-model="status" clearable placeholder="全部状态" aria-label="账号状态">
      <el-option label="已就绪" value="ready" /><el-option label="等待获取" value="pending" /><el-option label="异常" value="error" /><el-option label="已停用" value="disabled" />
    </el-select>
    <template #actions><span class="muted">{{ filtered.length }} / {{ accounts.length }} 个账号</span>
      <el-button :icon="Refresh" :disabled="!accounts.length" :loading="busy" @click="refreshAll">刷新全部</el-button>
    </template>
  </FilterBar>


  <EmptyState v-if="!accounts.length" title="还没有账号" description="粘贴 token 或导入本地脚本生成的 JSON。">
    <el-button type="primary" @click="importVisible = true">添加账号</el-button>
  </EmptyState>
  <EmptyState v-else-if="!filtered.length" title="没有符合条件的账号" description="试试其他名称或清空筛选条件。"><el-button @click="search = ''; status = ''">清空筛选</el-button></EmptyState>

  <div class="account-grid">
    <article v-for="a in paged" :key="a.id" class="panel account-card">
      <div class="card-top">
        <div class="account-avatar"><el-icon><Key /></el-icon></div>
        <div class="grow">
          <h2>{{ a.name }}</h2>
          <code class="muted">{{ a.token_hint }}</code>
        </div>
        <el-tag :type="a.status === 'ready' && a.enabled ? 'success' : a.status === 'error' || a.status === 'invalid' ? 'danger' : 'info'">
          {{ !a.enabled ? '已停用' : stateLabel[a.status] }}
        </el-tag>
      </div>
      <el-alert v-if="a.error || a.info_error" :title="a.error || a.info_error || ''" type="warning" :closable="false" show-icon class="mb" />
      <dl class="account-details">
        <div><dt>账号邮箱</dt><dd>{{ a.info.email || '上游未提供' }}</dd></div>
        <div><dt>会员状态</dt><dd>{{ a.info.is_vip === true ? 'VIP' : a.info.is_vip === false ? '普通账号' : '上游未提供' }}</dd></div>
        <div><dt>会员日期</dt><dd>{{ a.info.vip_date || '上游未提供' }}</dd></div>
        <div><dt>浏览器累计流量</dt><dd>{{ bytes(a.info.browser_traffic_total) }}</dd></div>
        <div><dt>节点 / 会话</dt><dd>{{ a.node_count }} / {{ a.pk_hint || '—' }}</dd></div>
        <div><dt>会话首次发现</dt><dd>{{ time(a.first_seen) }}</dd></div>
        <div><dt>最近成功获取</dt><dd>{{ time(a.last_success) }}</dd></div>
      </dl>
      <div class="mb">
        <el-tag v-if="a.stale" type="warning">缓存待更新</el-tag>
        <el-tag v-if="a.degraded" type="warning">仅解析主节点</el-tag>
      </div>
      <div class="card-actions">
        <el-button :loading="busy" :disabled="!a.enabled || a.status === 'invalid' || running.some(j => j.kind === 'refresh')" @click="refreshOne(a)">刷新</el-button>
        <el-button @click="edit(a)">编辑</el-button>
        <ActionMenu :disabled="busy" :items="[{ key: 'generate', label: '生成订阅' }, { key: 'remove', label: '删除账号', danger: true, divided: true }]" @select="key => key === 'generate' ? router.push({ name: 'subscriptions', query: { account: a.id } }) : remove(a)" />
      </div>
    </article>
  </div>

  <div v-if="filtered.length > pageSize" class="table-footer">
    <span>{{ filtered.length }} 个账号</span><el-pagination v-model:current-page="page" :page-size="pageSize" :pager-count="5" :total="filtered.length" background layout="prev, pager, next" />
  </div>
  <AccountImportDialog v-model:open="importVisible" />
  <AccountEditDialog v-model:open="editVisible" :account="editing" />
</template>
