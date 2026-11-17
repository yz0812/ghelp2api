<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { flagUrl, regionName, regionCountry } from '../regions'

const props = defineProps<{ country?: string, label?: string }>()
const code = computed(() => regionCountry(props.country, props.label))
const src = computed(() => flagUrl(code.value))
const failed = ref(false)
watch(src, () => { failed.value = false })
</script>

<template>
  <span class="region-badge" :data-country="code">
    <img v-if="src && !failed" :src="src" class="region-flag" alt="" aria-hidden="true" width="20" height="15" @error="failed = true" />
    <svg v-else class="region-unknown" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18M5 6.5h14M5 17.5h14" /></svg>
    <span>{{ regionName(label) }}</span>
  </span>
</template>

<style scoped>
.region-badge { display: inline-flex; align-items: center; gap: 7px; vertical-align: middle; max-width: 100%; }
.region-badge > span { min-width: 0; overflow-wrap: anywhere; }
.region-flag { flex: none; width: 20px; height: 15px; object-fit: cover; border-radius: 2px; box-shadow: 0 0 0 1px #80808030; }
.region-unknown { flex: none; width: 20px; height: 18px; fill: none; stroke: var(--muted); stroke-width: 1.4; }
</style>
