<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { CopyDocument, Download, Edit, FolderAdd, Link, Odometer, Plus } from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
import EmptyState from '../components/EmptyState.vue'
import AppDialog from '../components/AppDialog.vue'
import DeleteConversionButton from '../components/DeleteConversionButton.vue'
import SubscriptionDetailDialog from '../components/SubscriptionDetailDialog.vue'
import { api } from '../api'
import { action as refreshStore, load } from '../store'
import { copyText, downloadYaml, fetchConfig, time } from '../utils'
import type { ConversionRecord } from '../types'

const router = useRouter()
const search = ref('')
const status = ref('')
const page = ref(1)
const total = ref(0)
const rows = ref<ConversionRecord[]>([])
const loading = ref(true)
const error = ref('')
const retry = ref(0)
const manualUrl = ref('')
const candidates = ref<{id: string, name: string, likely: boolean, node_count: number}[]>([])
const adoptVisible = ref(false)
const adopting = ref(false)
const probeRecord = ref<ConversionRecord | null>(null)
const probeVisible = ref(false)
const templateLabel = { basic: '基础分流', enhanced: '增强分流', legacy: '旧版配置' } as const
let serial = 0

watch([search, status], () => { page.value = 1 })
watch([search, status, page, retry], async () => {
  const ticket = ++serial
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ q: search.value, page: String(page.value) })
    if (status.value) params.set('enabled', status.value)
    const data = await api('/convert/records?' + params)
    if (ticket === serial) {
      rows.value = data.items
      total.value = data.total
      page.value = Math.min(page.value, Math.max(1, Math.ceil(data.total / 20)))
    }
  } catch (e) {
    if (ticket === serial) error.value = (e as Error).message
  } finally {
    if (ticket === serial) loading.value = false
  }
}, { immediate: true })

async function useRecord(row: ConversionRecord, download: boolean) {
  try {
    const { clash_url } = await api('/convert/records/' + row.id + '/link')
    if (download) downloadYaml(await fetchConfig(clash_url))
    else if (!await copyText(clash_url)) manualUrl.value = clash_url
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function showCandidates() {
  try {
    candidates.value = await api('/convert/candidates')
    adoptVisible.value = true
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function adopt(id: string) {
  adopting.value = true
  try {
    await api('/convert/adopt/' + id, 'POST')
    await router.push({ name: 'conversion-detail', params: { id } })
    void refreshStore(load)
  } catch (e) { ElMessage.error((e as Error).message) }
  finally { adopting.value = false }
}
</script>

<template>
  <PageHeading title="Clash 订阅转换器" subtitle="管理已发布的在线订阅，或将节点转换为 Mihomo 配置。">
    <el-button :icon="FolderAdd" @click="showCandidates">纳入已有订阅</el-button>
    <el-button type="primary" :icon="Plus" @click="router.push({ name: 'conversion-new' })">新建转换</el-button>
  </PageHeading>
  <section class="panel records" v-loading="loading">
    <div class="panel-heading">
      <div><h2>在线记录</h2><p class="muted">共 {{ total }} 条，停用后订阅地址立即失效</p></div>
      <div class="record-filters">
        <el-input v-model="search" placeholder="搜索订阅名称" aria-label="搜索订阅名称" clearable />
        <el-select v-model="status" placeholder="全部状态" aria-label="订阅状态">
          <el-option label="全部状态" value="" /><el-option label="已启用" value="true" /><el-option label="已停用" value="false" />
        </el-select>
      </div>
    </div>
    <el-alert v-if="error" class="load-error" :title="error" type="error" :closable="false" role="alert">
      <el-button text type="primary" @click="retry++">重试</el-button>
    </el-alert>
    <EmptyState v-else-if="!loading && !rows.length" :title="search || status ? '没有匹配的记录' : '还没有在线订阅'"
      :description="search || status ? '调整名称或状态后再试。' : '新建转换并发布，或纳入以前生成的单文件订阅。'">
      <el-button v-if="!search && !status" type="primary" :icon="Plus" @click="router.push({ name: 'conversion-new' })">新建转换</el-button>
    </EmptyState>
    <article v-for="row in rows" v-else :key="row.id" class="record" :class="{ 'is-off': !row.enabled }">
      <span class="record-icon"><el-icon><Link /></el-icon></span>
      <div class="record-main">
        <div class="record-title">
          <router-link :to="{ name: 'conversion-detail', params: { id: row.id } }">{{ row.name }}</router-link>
          <span class="state-chip" :class="row.enabled ? 'is-ok' : ''"><i />{{ row.enabled ? '已启用' : '已停用' }}</span>
        </div>
        <p class="record-meta">
          <span><strong>{{ row.node_count }}</strong> 个节点</span>
          <span>{{ templateLabel[row.template] }}</span>
          <span>更新于 {{ time(row.updated_at) }}</span>
          <span v-if="row.created_at" class="hide-sm">创建于 {{ time(row.created_at) }}</span>
        </p>
      </div>
      <div class="record-actions">
        <el-button size="small" :icon="Odometer" @click="probeRecord = row; probeVisible = true">节点测速</el-button>
        <el-button size="small" :icon="CopyDocument" :disabled="!row.enabled" @click="useRecord(row, false)">复制链接</el-button>
        <el-button size="small" :icon="Download" :disabled="!row.enabled" @click="useRecord(row, true)">下载 YAML</el-button>
        <el-button size="small" :icon="Edit" @click="router.push({ name: 'conversion-edit', params: { id: row.id } })">编辑</el-button>
        <DeleteConversionButton size="small" :id="row.id" :name="row.name" @deleted="retry++" />
      </div>
    </article>
    <div v-if="total > 20" class="table-footer">
      <span>第 {{ page }} 页 · 共 {{ total }} 条</span>
      <el-pagination v-model:current-page="page" :total="total" :page-size="20" layout="prev, pager, next" />
    </div>
  </section>
  <SubscriptionDetailDialog v-model="probeVisible" :subscription="probeRecord" />
  <AppDialog :model-value="!!manualUrl" title="复制订阅链接" :form="false" @update:model-value="manualUrl = ''">
    <p class="muted">浏览器未允许自动复制，请手动复制下面的地址。</p>
    <el-input :model-value="manualUrl" readonly aria-label="订阅链接" @focus="($event.target as HTMLInputElement).select()" />
  </AppDialog>
  <AppDialog v-model="adoptVisible" title="纳入已有订阅" size="560px" :form="false">
    <p class="muted adopt-hint">选择要管理的单文件订阅。纳入后原订阅地址保持不变，已安装的客户端无需重新导入。</p>
    <EmptyState v-if="!candidates.length" title="暂无可纳入的订阅" description="只有单文件订阅可以纳入管理。" />
    <ul v-else class="candidate-list">
      <li v-for="item in candidates" :key="item.id" class="candidate">
        <span class="record-icon"><el-icon><Link /></el-icon></span>
        <div class="record-main">
          <strong>{{ item.name }}</strong>
          <p class="record-meta"><span>{{ item.node_count }} 个节点</span><span v-if="item.likely" class="likely">疑似历史转换记录</span></p>
        </div>
        <el-button size="small" type="primary" plain :disabled="adopting" @click="adopt(item.id)">纳入管理</el-button>
      </li>
    </ul>
  </AppDialog>
</template>

<style scoped>
.records { min-height: 14rem }
.records .panel-heading { margin-bottom: 0 }
.record-filters { display: flex; gap: 8px; flex-wrap: wrap }
.record-filters .el-input { width: 240px }
.record-filters .el-select { width: 130px }
.load-error { margin-top: 16px }
.record, .candidate { display: flex; align-items: center; gap: 14px; min-width: 0 }
.record { padding: 16px 12px; margin: 0 -12px; border-bottom: 1px solid var(--line); border-radius: 8px; transition: background .15s }
.record:last-of-type { border-bottom: 0 }
.record:hover { background: var(--surface-soft) }
.record-icon { display: grid; place-items: center; flex: none; width: 36px; height: 36px; border-radius: 9px; background: var(--accent-soft); color: var(--accent); font-size: 17px }
.record.is-off .record-icon { background: var(--surface-hover); color: var(--muted) }
.record-main { flex: 1; min-width: 0 }
.record-title { display: flex; align-items: center; gap: 10px; min-width: 0 }
.record-title a { color: var(--ink); font-size: 14px; font-weight: 600; text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap }
.record-title a:hover { color: var(--accent) }
.record-meta { display: flex; flex-wrap: wrap; gap: 4px 0; margin: 5px 0 0; color: var(--muted); font-size: 12px }
.record-meta > span + span::before { content: '·'; margin: 0 8px; color: var(--subtle) }
.record-meta strong { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums }
.record-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; flex: none }
.record-actions .el-button + .el-button { margin-left: 0 }
.adopt-hint { margin-bottom: 16px; line-height: 1.7 }
.candidate-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px }
.candidate { padding: 12px 14px; border: 1px solid var(--line); border-radius: 9px }
.candidate strong { font-size: 13px; font-weight: 600; overflow-wrap: anywhere }
.candidate .likely { color: var(--warning) }
@media (max-width: 900px) {
  .record { flex-wrap: wrap }
  .record-main { flex-basis: calc(100% - 50px) }
  .record-actions { justify-content: flex-start; padding-left: 50px; max-width: 100% }
}
@media (max-width: 767px) {
  .records .panel-heading { flex-direction: column; align-items: stretch }
  .record-filters .el-input, .record-filters .el-select { flex: 1 1 calc(50% - 4px); width: auto; min-width: 0 }
  .record-actions { padding-left: 0 }
  .hide-sm { display: none }
}
</style>
