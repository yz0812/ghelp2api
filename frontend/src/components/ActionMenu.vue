<script setup lang="ts">
import { ArrowDown } from '@element-plus/icons-vue'
import type { MenuAction } from '../types'
withDefaults(defineProps<{ items: MenuAction[], label?: string, disabled?: boolean }>(), { label: '更多操作' })
defineEmits<{ select: [key: string] }>()
</script>

<template>
  <el-dropdown trigger="click" :persistent="false" :disabled="disabled" @command="(value: string) => $emit('select', value)">
    <el-button :aria-label="label" :disabled="disabled">更多<el-icon class="menu-chevron"><ArrowDown /></el-icon></el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item v-for="item in items" :key="item.key" :command="item.key" :disabled="item.disabled" :divided="item.divided" :class="{ 'danger-action': item.danger }">{{ item.label }}</el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>
