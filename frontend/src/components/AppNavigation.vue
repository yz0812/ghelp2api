<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Connection, Files, Grid, Key, Link, Setting, Share, Tickets } from '@element-plus/icons-vue'
import { accounts } from '../store'
defineEmits<{ navigate: [] }>()
const route = useRoute()
const groups = [
  { label: '', items: [{ name: 'overview', label: '连接总览', icon: Grid }] },
  { label: '资源', items: [{ name: 'accounts', label: '账号管理', icon: Key }, { name: 'nodes', label: '节点列表', icon: Connection }, { name: 'sources', label: '订阅源', icon: Files }] },
  { label: '连接', items: [{ name: 'chains', label: '入口与出口', icon: Share }, { name: 'subscriptions', label: '订阅管理', icon: Link }] },
  { label: '系统', items: [{ name: 'jobs', label: '任务记录', icon: Tickets }, { name: 'settings', label: '系统设置', icon: Setting }] }
]
</script>

<template>
  <nav aria-label="主导航" class="app-nav">
    <div v-for="(group, index) in groups" :key="index" class="nav-group">
      <div v-if="group.label" class="nav-caption">{{ group.label }}</div>
      <router-link v-for="item in group.items" :key="item.name" :to="{ name: item.name }" :class="['nav-item', { active: route.name === item.name }]" :aria-label="item.label" :title="item.label" :aria-current="route.name === item.name ? 'page' : undefined" @click="$emit('navigate')">
        <el-icon><component :is="item.icon" /></el-icon><span class="nav-label">{{ item.label }}</span>
        <span v-if="item.name === 'accounts'" class="nav-count">{{ accounts.length }}</span>
      </router-link>
    </div>
  </nav>
</template>
