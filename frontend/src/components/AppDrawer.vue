<script setup lang="ts">
import { useMediaQuery } from '../composables/useMediaQuery'
import { clearError } from '../store'
import FormError from './FormError.vue'
withDefaults(defineProps<{ modelValue: boolean, title: string, size?: string, form?: boolean }>(), { size: '640px', form: true })
defineEmits<{ 'update:modelValue': [value: boolean], closed: [] }>()
const mobile = useMediaQuery('(max-width: 767px)')
</script>

<template>
  <el-drawer :model-value="modelValue" :title="title" :size="mobile ? '100%' : size" class="app-drawer" :close-on-click-modal="!form" @update:model-value="$emit('update:modelValue', $event)" @open="clearError" @closed="$emit('closed')">
    <FormError />
    <slot />
    <template v-if="$slots.footer" #footer><slot name="footer" /></template>
  </el-drawer>
</template>
