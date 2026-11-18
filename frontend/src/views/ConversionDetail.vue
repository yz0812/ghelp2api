<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Connection, CopyDocument, Download, Edit, Link, Odometer, Refresh, SwitchButton, View } from '@element-plus/icons-vue'
import DeleteConversionButton from '../components/DeleteConversionButton.vue'
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
const templateLabel = { basic: '基础分流', enhanced: '增强分流', legacy: '旧版配置' } as const

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

async function manage(action: 'toggle' | 'rotate') {
  if (!row.value || busy.value) return
  const { id, enabled } = row.value
  if (action === 'rotate') {
    try { await ElMessageBox.confirm('重置后所有客户端都需要导入新地址。', '重置链接', { type: 'warning' }) }
    catch { return }
  }
  busy.value = true
  try {
    if (action === 'rotate') await api('/subscriptions/' + id + '/rotate', 'POST')
    else await api('/convert/records/' + id + '?enabled=' + !enabled, 'PATCH')
    await refresh()
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
    <el-button :icon="ArrowLeft" @click="router.push({ name: 'converter' })">返回在线记录</el-button>
    <el-button v-if="row" type="primary" :icon="Edit" @click="router.push({ name: 'conversion-edit', params: { id } })">编辑记录</el-button>
  </PageHeading>
  <el-alert v-if="error" class="detail-error" :title="error" type="error" :closable="false" role="alert">
    <el-button text type="primary" @click="refresh">重试</el-button>
  </el-alert>
    <section v-if="row" class="panel" :aria-busy="busy">
      <div class="panel-heading">
        <div><h2>在线订阅地址</h2><p class="muted">在 Clash / Mihomo 客户端中导入此地址，内容更新后客户端刷新即可生效。</p></div>
        <span class="state-chip" :class="row.enabled ? 'is-ok' : ''"><i />{{ row.enabled ? '已启用' : '已停用' }}</span>
      </div>
      <div class="url-box" :class="{ 'is-off': !row.enabled }">
        <el-icon><Link /></el-icon>
        <input ref="link" class="url" aria-label="在线订阅地址" :value="row.clash_url" readonly @focus="link?.select()" />
        <el-button type="primary" :icon="CopyDocument" :disabled="!row.enabled" @click="copy">复制链接</el-button>
      </div>
      <p v-if="!row.enabled" class="off-hint">订阅已停用，客户端请求此地址将返回 404。</p>
      <div class="action-row">
        <el-button v-if="row.enabled" tag="a" :icon="Connection" :href="'clash://install-config?url=' + encodeURIComponent(row.clash_url)">导入 Clash</el-button>
        <el-button :icon="Odometer" @click="probeVisible = true">节点测速</el-button>
        <el-button :icon="Download" :disabled="!row?.enabled" @click="config('full')">下载 YAML</el-button>
        <el-button :icon="Download" :disabled="!row?.enabled" @click="config('nodes')">下载纯节点</el-button>
        <el-button :icon="View" :disabled="!row?.enabled" @click="config('preview')">预览配置</el-button>
      </div>
      <div v-if="preview" class="preview-block">
        <div class="preview-head"><span>配置预览</span><el-button text size="small" @click="preview = ''">收起</el-button></div>
        <pre class="config-preview" aria-label="配置预览">{{ preview }}</pre>
      </div>
    </section>
    <section v-if="row" class="panel">
      <div class="panel-heading"><div><h2>记录信息</h2></div></div>
      <dl class="info-list">
        <div><dt>节点数量</dt><dd>{{ row.node_count }} 个</dd></div>
        <div><dt>分流模板</dt><dd>{{ templateLabel[row.template] }}</dd></div>
        <div><dt>创建时间</dt><dd>{{ time(row.created_at) }}</dd></div>
        <div><dt>最后更新</dt><dd>{{ time(row.updated_at) }}</dd></div>
        <div><dt>关联引用</dt><dd>{{ row.references.length ? row.references.join('、') : '无' }}</dd></div>
      </dl>
      <details class="fold"><summary>查看节点 YAML</summary><pre class="config-preview">{{ row.content }}</pre></details>
    </section>
    <section v-if="row" class="panel danger-zone">
      <div class="panel-heading"><div><h2>管理</h2><p class="muted">以下操作会影响已导入此订阅的客户端。</p></div></div>
      <div class="manage-row">
        <div><strong>{{ row.enabled ? '停用订阅' : '恢复订阅' }}</strong><p class="muted">{{ row.enabled ? '暂停对外提供配置，可随时恢复，地址保持不变。' : '重新对外提供配置，原地址恢复可用。' }}</p></div>
        <el-button :icon="SwitchButton" :disabled="busy" @click="manage('toggle')">{{ row.enabled ? '停用' : '恢复' }}</el-button>
      </div>
      <div class="manage-row">
        <div><strong>重置链接</strong><p class="muted">生成新的订阅地址，旧地址立即失效，所有客户端需重新导入。</p></div>
        <el-button :icon="Refresh" :disabled="busy" @click="manage('rotate')">重置链接</el-button>
      </div>
      <div class="manage-row">
        <div><strong>删除记录</strong><p class="muted">{{ row.references.length ? '仍被「' + row.references.join('、') + '」引用，解除引用后才能删除。' : '删除后订阅地址永久失效，无法恢复。' }}</p></div>
        <DeleteConversionButton :id="row.id" :name="row.name" :disabled="busy || !!row.references.length"
          @deleted="router.push({ name: 'converter' })">删除记录</DeleteConversionButton>
      </div>
    </section>
  <SubscriptionDetailDialog v-model="probeVisible" :subscription="row ?? null" />
</template>

<style scoped>
.detail-error { margin-bottom: 16px }
.panel-heading { margin-bottom: 16px }
.url-box { display: flex; align-items: center; gap: 10px; padding: 6px 6px 6px 12px; background: var(--surface-soft); border: 1px solid var(--line); border-radius: 9px }
.url-box > .el-icon { flex: none; color: var(--accent) }
.url-box.is-off > .el-icon { color: var(--subtle) }
.url { flex: 1; min-width: 0; padding: 6px 0; border: 0; outline: none; background: transparent; color: var(--ink); font-family: Consolas, "SFMono-Regular", monospace; font-size: 12px; text-overflow: ellipsis }
.url-box.is-off .url { color: var(--muted); text-decoration: line-through }
.off-hint { margin: 8px 0 0; color: var(--warning); font-size: 12px }
.action-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px }
.action-row .el-button + .el-button { margin-left: 0 }
.preview-block { margin-top: 16px }
.preview-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; color: var(--muted); font-size: 12px }
.config-preview { white-space: pre-wrap; overflow-wrap: anywhere }
.info-list { margin-top: 0 }
.info-list > div { padding: 12px 0 }
.fold { margin-top: 12px; padding-top: 14px; border-top: 1px solid var(--line) }
.fold summary { cursor: pointer; font-size: 13px; color: var(--muted) }
.fold summary:hover { color: var(--ink) }
.fold .config-preview { margin-top: 10px; max-height: 24rem }
.manage-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--line) }
.manage-row:last-child { border-bottom: 0; padding-bottom: 0 }
.manage-row strong { font-size: 13px; font-weight: 600 }
.manage-row p { margin: 4px 0 0; font-size: 12px; overflow-wrap: anywhere }
.manage-row .el-button { flex: none }
@media (max-width: 767px) {
  .url-box { flex-wrap: wrap; padding: 10px }
  .url-box .el-button { width: 100% }
  .manage-row { flex-direction: column; align-items: stretch; gap: 10px }
}
</style>
