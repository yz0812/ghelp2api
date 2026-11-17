<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Connection, Expand, Fold, Menu, SwitchButton } from '@element-plus/icons-vue'
import { booting, expire, logout, settings } from './store'
import AppNavigation from './components/AppNavigation.vue'
import ThemeSwitcher from './components/ThemeSwitcher.vue'
import FormError from './components/FormError.vue'
import { useMediaQuery } from './composables/useMediaQuery'

const router = useRouter()
const route = useRoute()
const contentRef = ref<HTMLElement>()
const mobileNav = ref(false)
const compact = ref(false)
try { compact.value = localStorage.getItem('ghelper.sidebar') === 'compact' } catch {}
const mobile = useMediaQuery('(max-width: 767px)')
const layout = () => ['login', 'not-found'].includes(String(route.name))
watch(() => route.fullPath, () => { contentRef.value?.scrollTo({ top: 0 }); mobileNav.value = false })
watch(mobile, () => { mobileNav.value = false })

function toggleSidebar() {
  compact.value = !compact.value
  try { localStorage.setItem('ghelper.sidebar', compact.value ? 'compact' : 'expanded') } catch {}
}
function onExpired() { expire(); router.push({ name: 'login' }) }
async function signOut() { await logout(); router.push({ name: 'login' }) }
window.addEventListener('session-expired', onExpired)
onUnmounted(() => window.removeEventListener('session-expired', onExpired))
</script>

<template>
  <div v-if="booting" class="boot" role="status">
    <span class="brand-mark"><el-icon><Connection /></el-icon></span>
    <p>正在加载管理台</p>
    <el-skeleton :rows="3" animated class="boot-skeleton" />
  </div>
  <router-view v-else-if="layout()" />
  <div v-else :class="['layout', { 'is-compact': compact }]">
    <a href="#main-content" class="skip-link">跳转到主要内容</a>
    <aside class="sidebar">
      <router-link :to="{ name: 'overview' }" class="brand" aria-label="GHelper 首页">
        <span class="brand-mark"><el-icon><Connection /></el-icon></span><span class="brand-name">GHelper<small>连接管理台</small></span>
      </router-link>
      <AppNavigation />
      <div class="sidebar-bottom"><span class="status-dot" /><span class="sidebar-note">本地工作空间<small>v{{ settings.version || '—' }}</small></span></div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="top-location">
          <el-button v-if="mobile" text :icon="Menu" aria-label="打开导航" @click="mobileNav = true" />
          <el-button v-else class="sidebar-toggle" text :icon="compact ? Expand : Fold" :aria-label="compact ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar" />
          <div class="breadcrumb"><span class="breadcrumb-root">工作空间 <span>/</span></span><strong>{{ route.meta.label }}</strong></div>
        </div>
        <div class="top-actions">
          <ThemeSwitcher /><span class="top-divider" /><span class="admin-badge" aria-label="管理员">A</span>
          <el-button text :icon="SwitchButton" @click="signOut">退出</el-button>
        </div>
      </header>
      <main id="main-content" ref="contentRef" class="content" tabindex="-1">
        <FormError /><router-view />
        <footer class="page-footer"><span>GHelper · 连接由你掌控</span><span>数据保存在本地</span></footer>
      </main>
    </div>
    <el-drawer v-model="mobileNav" title="工作空间" direction="ltr" size="min(280px, 88vw)" class="navigation-drawer">
      <AppNavigation @navigate="mobileNav = false" />
    </el-drawer>
  </div>
</template>
