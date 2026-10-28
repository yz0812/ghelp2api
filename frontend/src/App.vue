<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Connection, Files, Grid, Key, Link, Setting, SwitchButton, Tickets } from '@element-plus/icons-vue'
import { accounts, booting, error, expire, logout, settings } from './store'

const router = useRouter()
const route = useRoute()
const contentRef = ref<HTMLElement>()

const nav = [
  { name: 'overview', label: '连接总览', icon: Grid },
  { name: 'accounts', label: '账号管理', icon: Key },
  { name: 'nodes', label: '节点列表', icon: Connection },
  { name: 'sources', label: '订阅源', icon: Files },
  { name: 'subscriptions', label: '订阅管理', icon: Link },
  { name: 'jobs', label: '任务记录', icon: Tickets },
  { name: 'settings', label: '系统设置', icon: Setting }
]

const layout = () => ['login', 'not-found'].includes(String(route.name))
watch(() => route.fullPath, () => contentRef.value?.scrollTo({ top: 0 }))

function onExpired() { expire(); router.push({ name: 'login' }) }
async function signOut() {
  await logout()
  router.push({ name: 'login' })
}
window.addEventListener('session-expired', onExpired)
onUnmounted(() => window.removeEventListener('session-expired', onExpired))
</script>

<template>
  <div v-if="booting" class="boot">
    <el-icon class="is-loading" :size="32"><Connection /></el-icon>
    <p>正在加载管理台</p>
  </div>
  <router-view v-else-if="layout()" />
  <div v-else class="layout">
    <aside class="sidebar">
      <router-link :to="{ name: 'overview' }" class="brand">
        <el-icon><Connection /></el-icon> GHelper<span class="brand-small">WEB</span>
      </router-link>
      <div class="nav-caption">工作空间</div>
      <nav aria-label="主导航">
        <router-link
          v-for="item in nav" :key="item.name"
          :to="{ name: item.name }"
          :class="['nav-item', { active: route.name === item.name }]"
          :aria-label="item.label"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
          <span v-if="item.name === 'accounts'" class="nav-count">{{ accounts.length }}</span>
        </router-link>
      </nav>
      <div class="sidebar-bottom">
        <span class="status-dot"></span> 独立部署 · 数据本地保存
        <p>GHelper Clash Web {{ settings.version || '—' }}</p>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb">工作空间 <span>/</span> <strong>{{ route.meta.label }}</strong></div>
        <div class="top-actions">
          <span class="admin-badge">A</span><span>管理员</span>
          <el-button text :icon="SwitchButton" @click="signOut">退出</el-button>
        </div>
      </header>
      <main ref="contentRef" class="content">
        <el-alert v-if="error" :title="error" type="error" show-icon role="alert" class="mb" @close="error = ''" />
        <router-view />
      </main>
      <footer class="page-footer">GHelper Clash Web <span>连接由你掌控</span></footer>
    </div>
  </div>
</template>
