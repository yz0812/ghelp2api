<script setup lang="ts">
import { FolderOpened } from '@element-plus/icons-vue'
defineProps<{ title: string, description?: string }>()
</script>

<template>
  <div class="empty-state" role="status">
    <span class="empty-icon"><el-icon><FolderOpened /></el-icon></span>
    <h3>{{ title }}</h3>
    <p v-if="description" class="muted">{{ description }}</p>
    <div v-if="$slots.default" class="empty-actions"><slot /></div>
  </div>
</template>
