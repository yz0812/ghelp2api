<script setup lang="ts">
defineProps<{ title: string, subtitle: string }>()
</script>

<template>
  <div class="page-heading">
    <div>
      <span class="eyebrow">GHELPER WORKSPACE</span>
      <h1>{{ title }}</h1>
      <p class="muted">{{ subtitle }}</p>
    </div>
    <slot />
  </div>
</template>
