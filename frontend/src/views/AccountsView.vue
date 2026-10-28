<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Key, Plus, Refresh } from '@element-plus/icons-vue'
import { api } from '../api'
import PageHeading from '../components/PageHeading.vue'
import AccountImportDialog from '../components/AccountImportDialog.vue'
import AccountEditDialog from '../components/AccountEditDialog.vue'
import { accounts, action, busy, load, running, schedulePoll, stateLabel } from '../store'
import { bytes, time } from '../utils'
import type { Account } from '../types'

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

  <div class="toolbar">
    <span class="muted">共 {{ accounts.length }} 个账号</span>
    <el-button :icon="Refresh" :disabled="!accounts.length" :loading="busy" @click="refreshAll">刷新全部</el-button>
  </div>

  <el-empty v-if="!accounts.length" description="粘贴 token 或导入本地脚本生成的 JSON">
    <el-button type="primary" @click="importVisible = true">添加账号</el-button>
  </el-empty>

  <div class="account-grid">
    <article v-for="a in accounts" :key="a.id" class="panel account-card">
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
        <el-button type="primary" plain @click="router.push({ name: 'subscriptions', query: { account: a.id } })">生成订阅</el-button>
        <el-button text type="danger" @click="remove(a)">删除</el-button>
      </div>
    </article>
  </div>

  <AccountImportDialog v-model:open="importVisible" />
  <AccountEditDialog v-model:open="editVisible" :account="editing" />
</template>
