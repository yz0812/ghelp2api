<template>
  <div class="filter-bar">
    <div class="filter-controls"><slot /></div>
    <div v-if="$slots.actions" class="toolbar-actions"><slot name="actions" /></div>
  </div>
</template>
