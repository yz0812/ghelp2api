<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection, Key, Link, Plus, Refresh } from '@element-plus/icons-vue'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import { jobKind, time } from '../utils'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import AccountImportDialog from '../components/AccountImportDialog.vue'
import { accounts, action, busy, chains, healthy, invalidSources, jobs, load, regions, running, schedulePoll, settings, stateLabel, subscriptions, usable } from '../store'

const importVisible = ref(false)
const failedAccounts = computed(() => accounts.value.filter(a => a.enabled && ['invalid', 'error'].includes(a.status)))
const failedChains = computed(() => chains.value.filter(c => c.enabled && c.status.state === 'error'))
const recentJobs = computed(() => jobs.value.slice(0, 4))

async function refreshAll() {
  await action(async () => {
    await api('/accounts/all/refresh', 'POST')
    await load()
    schedulePoll()
    ElMessage.success('刷新任务已提交')
  })
}
</script>

<template>
  <PageHeading title="连接总览" subtitle="账号、节点与订阅，一个清晰的连接视图。">
    <el-button type="primary" :icon="Plus" @click="importVisible = true">添加账号</el-button>
  </PageHeading>

  <RunningBanner :running="running.length > 0" />

  <section class="stats-grid">
    <article class="stat">
      <span>管理账号</span>
      <div>{{ accounts.length }} <small>个</small></div>
      <p><span class="status-dot" />{{ healthy }} 个已就绪</p>
      <el-icon><Key /></el-icon>
    </article>
    <article class="stat">
      <span>当前节点</span>
      <div>{{ usable.length }} <small>个</small></div>
      <p>{{ regions.length }} 个入口代号</p>
      <el-icon><Connection /></el-icon>
    </article>
    <article class="stat">
      <span>订阅配置</span>
      <div>{{ subscriptions.length }} <small>份</small></div>
      <p>独立订阅与多账号聚合</p>
      <el-icon><Link /></el-icon>
    </article>
    <article class="stat">
      <span>代理链路</span>
      <div>{{ chains.length }} <small>条</small></div>
      <p>{{ chains.filter(c => c.enabled).length }} 条已启用</p>
      <el-icon><Refresh /></el-icon>
    </article>
  </section>

  <div class="run-summary">
    <span><span class="status-dot" />自动刷新 · 每 {{ settings.refresh_minutes }} 分钟</span>
    <router-link :to="{ name: 'settings' }" class="text-link">调整运行设置 →</router-link>
  </div>
  <div v-if="failedAccounts.length || invalidSources.length || failedChains.length" class="run-summary">
    <span class="danger-text">需要关注</span>
    <router-link v-if="failedAccounts.length" :to="{ name: 'accounts' }" class="text-link">{{ failedAccounts.length }} 个账号异常</router-link>
    <router-link v-if="invalidSources.length" :to="{ name: 'sources' }" class="text-link">{{ invalidSources.length }} 个订阅源失效</router-link>
    <router-link v-if="failedChains.length" :to="{ name: 'chains' }" class="text-link">{{ failedChains.length }} 条链路异常</router-link>
  </div>
  <div class="overview-grid">
  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>账号连接状态</h2>
        <p class="muted">最近获取结果与节点数量</p>
      </div>
      <el-button :icon="Refresh" :loading="busy" :disabled="!accounts.length" @click="refreshAll">刷新全部</el-button>
    </div>
    <EmptyState v-if="!accounts.length" title="从第一个账号开始" description="添加账号后，即可获取节点并创建统一订阅。">
      <el-button type="primary" @click="importVisible = true">添加账号</el-button>
    </EmptyState>
    <div v-else class="account-overview">
      <div v-for="a in accounts" :key="a.id" class="account-row">
        <div class="account-avatar"><el-icon><Key /></el-icon></div>
        <div class="grow">
          <strong>{{ a.name }}</strong>
          <p class="muted">{{ a.info.email || a.token_hint }}</p>
        </div>
        <el-tag :type="a.enabled && a.status === 'ready' ? 'success' : a.status === 'invalid' || a.status === 'error' ? 'danger' : 'info'" effect="light">
          {{ !a.enabled ? '已停用' : stateLabel[a.status] }}
        </el-tag>
        <span class="node-counter">{{ a.node_count }} 节点</span>
        <router-link :to="{ name: 'accounts' }" class="text-link">管理 <el-icon><ArrowRight /></el-icon></router-link>
      </div>
    </div>
  </section>

  <section class="panel">
    <div class="panel-heading"><div><h2>最近任务</h2><p class="muted">刷新、测速与链路检测</p></div><router-link :to="{ name: 'jobs' }" class="text-link">全部 →</router-link></div>
    <EmptyState v-if="!recentJobs.length" title="暂无任务" description="执行刷新或测速后，进度会显示在这里。" />
    <div v-for="job in recentJobs" :key="job.id" class="activity-row">
      <div><strong>{{ jobKind[job.kind] || job.kind }}</strong><p>{{ time(job.created_at) }}</p><p>{{ job.completed }} / {{ job.total }}</p></div>
      <el-tag :type="job.status === 'completed' ? 'success' : ['partial', 'interrupted'].includes(job.status) ? 'warning' : 'info'">{{ stateLabel[job.status] || job.status }}</el-tag>
    </div>
  </section>
  </div>

  <div class="guide-grid">
    <article class="guide-card">
      <el-icon :size="28"><Link /></el-icon>
      <h3>让配置自动更新</h3>
      <p>创建订阅并复制地址到 Clash 客户端。服务会定期获取节点，无需重复导入文件。</p>
      <router-link :to="{ name: 'subscriptions' }" class="text-link">前往订阅管理 →</router-link>
    </article>
    <article class="guide-card soft">
      <el-icon :size="28"><Connection /></el-icon>
      <h3>连接你的入口与出口</h3>
      <p>从已有订阅选择入口，连接自建或第三方出口。生成链路订阅，或通过中转地址直接使用。</p>
      <router-link :to="{ name: 'chains' }" class="text-link">配置代理链路 →</router-link>
    </article>
  </div>

  <AccountImportDialog v-model:open="importVisible" />
</template>
