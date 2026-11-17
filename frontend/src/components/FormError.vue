<script setup lang="ts">
import { error } from '../store'
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" show-icon role="alert" class="mb" @close="error = ''" />
</template>
