<script setup lang="ts">
defineProps<{ hops: { name: string, source: string, candidates?: number }[], label?: string }>()
function role(index: number, total: number) {
  return index === 0 ? '入口' : index === total - 1 ? '出口' : '中转'
}
</script>

<template>
  <ol class="path-preview" :aria-label="label || '代理路径'">
    <li v-for="(hop, index) in hops" :key="index" :class="['path-pill', 'is-' + hop.source]">
      <small>{{ role(index, hops.length) }}</small>
      <span>{{ hop.name }}</span>
      <em v-if="hop.source === 'entry' && hop.candidates">{{ hop.candidates }} 候选</em>
    </li>
  </ol>
</template>

<style scoped>
.path-preview { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 22px; margin: 0; padding: 0; list-style: none; min-width: 0; }
.path-pill { position: relative; display: inline-flex; align-items: center; gap: 6px; max-width: 100%; min-width: 0; padding: 4px 10px 4px 4px; border: 1px solid var(--line); border-radius: 999px; background: var(--surface-soft); font-size: 12px; line-height: 1.4; }
.path-pill + .path-pill::before { content: ""; position: absolute; left: -18px; top: 50%; width: 14px; border-top: 1.5px solid var(--subtle); }
.path-pill + .path-pill::after { content: ""; position: absolute; left: -8px; top: calc(50% - 3.5px); border: 3.5px solid transparent; border-left: 5px solid var(--subtle); border-right: 0; }
.path-pill small { flex: none; padding: 1px 7px; border-radius: 999px; font-size: 11px; background: var(--surface-hover); color: var(--muted); }
.path-pill.is-entry small { background: var(--accent-soft); color: var(--accent); }
.path-pill:last-child small { background: var(--success-soft); color: var(--success); }
.path-pill > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
.path-pill em { flex: none; font-style: normal; color: var(--muted); font-size: 11px; }
</style>
