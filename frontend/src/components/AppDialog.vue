<script setup lang="ts">
import { clearError } from '../store'
import FormError from './FormError.vue'
withDefaults(defineProps<{ modelValue: boolean, title: string, size?: string, form?: boolean }>(), { size: '640px', form: true })
defineEmits<{ 'update:modelValue': [value: boolean], closed: [] }>()
</script>

<template>
  <el-dialog append-to-body align-center :model-value="modelValue" :title="title" :width="size" class="app-dialog" :close-on-click-modal="!form" @update:model-value="$emit('update:modelValue', $event)" @open="clearError" @closed="$emit('closed')">
    <FormError />
    <slot />
    <template v-if="$slots.footer" #footer><slot name="footer" /></template>
  </el-dialog>
</template>
