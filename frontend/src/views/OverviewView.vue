<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection, Key, Link, Plus, Refresh } from '@element-plus/icons-vue'
import { api } from '../api'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import AccountImportDialog from '../components/AccountImportDialog.vue'
import { accounts, action, busy, healthy, load, regions, running, schedulePoll, settings, stateLabel, subscriptions, usable } from '../store'

const importVisible = ref(false)

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
      <span>自动刷新</span>
      <div>{{ settings.refresh_minutes }} <small>分钟</small></div>
      <p>服务端决定会话是否轮换</p>
      <el-icon><Refresh /></el-icon>
    </article>
  </section>

  <section class="panel">
    <div class="panel-heading">
      <div>
        <h2>账号连接状态</h2>
        <p class="muted">最近获取结果与节点数量</p>
      </div>
      <el-button :icon="Refresh" :loading="busy" :disabled="!accounts.length" @click="refreshAll">刷新全部</el-button>
    </div>
    <el-empty v-if="!accounts.length" description="还没有账号，添加第一个 token 开始使用">
      <el-button type="primary" @click="importVisible = true">添加账号</el-button>
    </el-empty>
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

  <div class="guide-grid">
    <article class="guide-card">
      <el-icon :size="28"><Link /></el-icon>
      <h3>让配置自动更新</h3>
      <p>创建订阅并复制地址到 Clash 客户端。服务会定期获取节点，无需重复导入文件。</p>
      <router-link :to="{ name: 'subscriptions' }" class="text-link">前往订阅管理 →</router-link>
    </article>
    <article class="guide-card soft">
      <el-icon :size="28"><Connection /></el-icon>
      <h3>入口不等于出口</h3>
      <p>广州、佛山等代号表示国内接入入口。测速反映本服务所在网络的连接耗时，不代表带宽。</p>
      <router-link :to="{ name: 'nodes' }" class="text-link">查看节点详情 →</router-link>
    </article>
  </div>

  <AccountImportDialog v-model:open="importVisible" />
</template>
