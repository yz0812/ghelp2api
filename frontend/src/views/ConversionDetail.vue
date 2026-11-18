<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import PageHeading from '../components/PageHeading.vue'
import SubscriptionDetailDialog from '../components/SubscriptionDetailDialog.vue'
import { api } from '../api'
import { action as refreshStore, load } from '../store'
import { copyText, fetchConfig, downloadYaml, time } from '../utils'
import type { ConversionDetail } from '../types'

const props = defineProps<{ id: string }>()
const router = useRouter()
const row = ref<ConversionDetail>()
const busy = ref(false)
const error = ref('')
const preview = ref('')
const probeVisible = ref(false)
const link = ref<HTMLInputElement>()

async function refresh() {
  const id = props.id
  error.value = ''
  preview.value = ''
  try {
    const data = await api('/convert/records/' + id)
    if (id === props.id) row.value = data
  } catch (e) { if (id === props.id) error.value = (e as Error).message }
}
watch(() => props.id, () => { probeVisible.value = false; row.value = undefined; void refresh() }, { immediate: true })

async function manage(action: 'toggle' | 'rotate' | 'delete') {
  if (!row.value || busy.value) return
  const { id, enabled } = row.value
  if (action !== 'toggle') {
    const message = action === 'delete' ? '删除后订阅地址永久失效。' : '重置后所有客户端都需要导入新地址。'
    try { await ElMessageBox.confirm(message, action === 'delete' ? '删除记录' : '重置链接', { type: 'warning' }) }
    catch { return }
  }
  busy.value = true
  try {
    if (action === 'delete') {
      await api('/subscriptions/' + id, 'DELETE')
      await router.push({ name: 'converter' })
    } else {
      if (action === 'rotate') await api('/subscriptions/' + id + '/rotate', 'POST')
      else await api('/convert/records/' + id + '?enabled=' + !enabled, 'PATCH')
      await refresh()
    }
    void refreshStore(load)
  } catch (e) { ElMessage.error((e as Error).message) }
  finally { busy.value = false }
}

async function config(action: 'preview' | 'full' | 'nodes') {
  if (!row.value) return
  try {
    const yaml = await fetchConfig(action === 'nodes' ? row.value.proxies_url : row.value.clash_url)
    if (action === 'preview') preview.value = yaml
    else downloadYaml(yaml)
  } catch (e) { ElMessage.error((e as Error).message) }
}

async function copy() {
  if (row.value && !await copyText(row.value.clash_url)) {
    link.value?.select()
    ElMessage.info('请复制选中的订阅地址')
  }
}
</script>

<template>
  <PageHeading :title="row?.name || '在线订阅详情'" :subtitle="row ? row.node_count + ' 个节点 · 更新于 ' + time(row.updated_at) : '读取在线记录'">
    <el-button @click="router.push({ name: 'converter' })">返回在线记录</el-button>
    <el-button v-if="row" type="primary" @click="router.push({ name: 'conversion-edit', params: { id } })">编辑记录</el-button>
  </PageHeading>
  <p v-if="error" role="alert">{{ error }} <el-button text @click="refresh">重试</el-button></p>
  <section v-if="row" class="panel details" :aria-busy="busy">
    <div class="card-actions"><h2>在线订阅地址</h2><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '已启用' : '已停用' }}</el-tag></div>
    <input ref="link" class="url" aria-label="在线订阅地址" :value="row.clash_url" readonly @focus="link?.select()" />
    <div class="card-actions">
      <el-button @click="probeVisible = true">节点测速</el-button>
      <el-button :disabled="!row?.enabled" @click="copy">复制链接</el-button>
      <el-button v-if="row.enabled" tag="a" :href="'clash://install-config?url=' + encodeURIComponent(row.clash_url)">导入 Clash</el-button>
      <el-button :disabled="!row?.enabled" @click="config('full')">下载 YAML</el-button>
      <el-button :disabled="!row?.enabled" @click="config('nodes')">下载纯节点</el-button>
      <el-button :disabled="!row?.enabled" @click="config('preview')">预览配置</el-button>
    </div>
    <details><summary>查看节点 YAML</summary><pre>{{ row?.content }}</pre></details>
    <pre v-if="preview" aria-label="配置预览">{{ preview }}</pre>
    <div class="card-actions">
      <el-button @click="manage('toggle')">{{ row?.enabled ? '停用' : '恢复' }}</el-button>
      <el-button @click="manage('rotate')">重置链接</el-button>
      <span v-if="row?.references.length" class="muted">关联引用：{{ row.references.join('、') }}</span>
      <el-button type="danger" plain :disabled="!!row?.references.length" @click="manage('delete')">删除记录</el-button>
    </div>
  </section>
  <SubscriptionDetailDialog v-model="probeVisible" :subscription="row ?? null" />
</template>

<style scoped>
.details { padding: 1.25rem; display: grid; gap: 1rem }
.url { width: 100%; min-width: 0; box-sizing: border-box; padding: .65rem; border: 1px solid var(--el-border-color); border-radius: 6px; color: inherit; background: var(--el-fill-color-blank) }
pre { max-height: 24rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere }
summary { cursor: pointer }
</style>
