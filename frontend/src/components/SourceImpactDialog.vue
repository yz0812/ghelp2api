<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getSourceImpact } from '../api'
import { chains, subscriptions } from '../store'
import type { Source, SourceImpact } from '../types'
import { time } from '../utils'

const props = defineProps<{ source: Source }>()
const emit = defineEmits<{ close: [] }>()
const data = ref<SourceImpact>(), error = ref('')
const statusText = {
  baseline: '尚无可比较的两次刷新', pending: '正在分析本次刷新', unchanged: '本次刷新未改变节点身份或有效候选',
  analyzed: '本次刷新分析完成', failed: '本次刷新成功，影响分析失败', incomplete: '上次变更的影响分析未完成'
}
const levels: Record<string, string> = { primary_lost: '主路径受损', backup_lost: '备用路径受损',
  all_lost: '全部路径引用失效', restored: '路径引用恢复', candidates: '候选节点变化' }
const reasons: Record<string, string> = { missing: '节点引用失效', restored: '节点引用恢复',
  reduced: '候选减少', expanded: '候选增加', replaced: '候选已更换' }

onMounted(async () => {
  try { data.value = await getSourceImpact(props.source.id) }
  catch (e) { error.value = (e as Error).message }
})
</script>

<template>
  <el-dialog :model-value="true" append-to-body align-center width="min(620px, 94vw)" :title="source.name + ' · 刷新影响'" @close="emit('close')">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-skeleton v-else-if="!data" :rows="3" animated />
    <template v-else>
      <el-alert v-if="source.last_error" :title="'最近刷新失败：' + source.last_error" type="warning" :closable="false" />
      <p v-if="!source.last_error">{{ statusText[data.status] }}</p>
      <template v-if="data.report">
        <p class="muted">最近变更报告 · {{ time(data.report.analyzed_at) }} · 源版本 {{ data.report.version }}。以下为刷新时的节点引用情况，网络可达性需另行检测。</p>
        <p>源节点新增 {{ data.report.added }} 个，移除 {{ data.report.removed }} 个。</p>
        <p v-for="s in data.report.subscriptions" :key="s.id">
          订阅「{{ subscriptions.find(item => item.id === s.id)?.name || s.id }}」{{ s.enabled ? '' : '（当时已停用）' }}新增 {{ s.added }} 个候选，移除 {{ s.removed }} 个。
        </p>
        <p v-if="!data.report.chains.length">此次变更未影响链路引用。</p>
        <section v-for="c in data.report.chains" :key="c.id" class="impact-chain">
          <h3>{{ chains.find(item => item.id === c.id)?.name || c.id }}{{ c.enabled ? '' : '（当时已停用）' }} · {{ levels[c.level] }}</h3>
          <p>引用完整的路径 {{ c.complete_paths }} / {{ c.total_paths }}</p>
          <p v-for="p in c.paths" :key="p.path_index">
            <strong>{{ p.path_index ? '备用路径 ' + p.path_index : '主路径' }}</strong> · {{ p.after ? '引用完整' : '引用不完整' }}
            <span v-for="h in p.hops" :key="h.hop_index" class="impact-hop">
              第 {{ h.hop_index + 1 }} 跳 · {{ reasons[h.reason] }}（{{ h.before }} → {{ h.after }}）
              <router-link :to="{ name: 'chain-edit', params: { id: c.id }, query: { revision: c.revision, path: p.path_index, hop: h.hop_index } }">定位此跳</router-link>
            </span>
          </p>
        </section>
      </template>
    </template>
  </el-dialog>
</template>

<style scoped>
.impact-chain { border-top: 1px solid var(--line); padding-top: 12px; }
.impact-hop { display: flex; flex-wrap: wrap; gap: 8px; margin-block: 6px; }
</style>
