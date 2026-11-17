<script setup lang="ts">
import { Monitor, Moon, Sunny } from '@element-plus/icons-vue'
import { useTheme } from '../composables/useTheme'
const { preference, setTheme } = useTheme()
const choices = [
  { value: 'light' as const, label: '浅色', icon: Sunny },
  { value: 'dark' as const, label: '深色', icon: Moon },
  { value: 'system' as const, label: '跟随系统', icon: Monitor }
]
</script>

<template>
  <el-dropdown trigger="click" :persistent="false" @command="setTheme">
    <el-button text class="theme-trigger" aria-label="切换外观">
      <el-icon><component :is="choices.find(c => c.value === preference)!.icon" /></el-icon>
      <span class="theme-label">{{ choices.find(c => c.value === preference)!.label }}</span>
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item v-for="choice in choices" :key="choice.value" :command="choice.value" :icon="choice.icon" :class="{ 'theme-selected': preference === choice.value }">
          {{ choice.label }}<span v-if="preference === choice.value" class="sr-only">（当前）</span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>
