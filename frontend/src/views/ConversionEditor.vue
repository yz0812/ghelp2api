<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Check, Download, Upload, View } from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
import { api } from '../api'
import { action as refreshStore, load } from '../store'
import { downloadYaml, fetchConfig } from '../utils'
import type { ConversionDetail } from '../types'

interface Preview {
  count: number
  issues: string[]
  yaml: string
  changes: { added: number, removed: number, modified: number } | null
}

const props = defineProps<{ id?: string }>()
const router = useRouter()
const name = ref('')
const content = ref('')
const template = ref<'basic' | 'enhanced'>('basic')
const format = ref<'full' | 'nodes'>('full')
const record = ref<ConversionDetail>()
const result = ref<Preview>()
const partial = ref(false)
const busy = ref(false)
const error = ref('')
const conflict = ref(false)
const existingYaml = ref('')
const filePicker = ref<HTMLInputElement>()
const requestKey = ref('')
const fields = computed(() => ({ name: name.value.trim(), content: content.value, template: template.value }))
const lineCount = computed(() => content.value.split('\n').filter(line => line.trim()).length)
const templates = [
  { value: 'basic', title: '基础分流', description: '国内直连，其余走代理，无外部依赖。' },
  { value: 'enhanced', title: '增强分流', description: 'Apple / Google / 广告规则，客户端需下载外部规则源。' },
] as const
const canSave = computed(() => !!result.value?.count && (!result.value.issues.length || partial.value))
watch([content, template], () => { result.value = undefined; partial.value = false })
watch([name, content, template, partial], () => { requestKey.value = '' })

async function init() {
  const id = props.id
  conflict.value = false
  record.value = undefined
  existingYaml.value = ''
  name.value = content.value = error.value = ''
  template.value = 'basic'
  result.value = undefined
  if (!id) return
  busy.value = true
  try {
    const data: ConversionDetail = await api('/convert/records/' + id)
    if (id !== props.id) return
    record.value = data
    name.value = data.name
    content.value = data.content
    template.value = data.template === 'enhanced' ? 'enhanced' : 'basic'
    if (data.template === 'legacy' && data.enabled) existingYaml.value = await fetchConfig(data.clash_url)
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
watch(() => props.id, init, { immediate: true })

async function reviewLatest() {
  busy.value = true
  try {
    record.value = await api('/convert/records/' + props.id)
    result.value = undefined
    partial.value = false
    conflict.value = false
    error.value = ''
    ElMessage.info('已载入最新版本。当前输入已保留，请重新预览差异。')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}

async function pick(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    if (file.size > 24_000_000) throw new Error('文件过大')
    content.value = await file.text()
  } catch (e) { ElMessage.error((e as Error).message) }
  input.value = ''
}

async function execute(action: 'preview' | 'save' | 'download') {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const body = { ...fields.value, allow_partial: partial.value }
    if (action === 'preview') {
      result.value = await api('/convert/preview' + (props.id ? '?record_id=' + props.id : ''), 'POST', body)
    } else if (action === 'download') {
      downloadYaml(format.value === 'full' && result.value ? result.value.yaml :
        await api('/convert?full=false', 'POST', body))
    } else {
      let saved
      if (props.id && record.value) {
        saved = await api('/convert/records/' + props.id, 'PUT', { ...body, version: record.value.version })
      } else {
        if (!requestKey.value) requestKey.value = Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-')
        saved = await api('/convert/publish', 'POST', { ...body, request_key: requestKey.value })
      }
      await router.push({ name: 'conversion-detail', params: { id: saved.id } })
      void refreshStore(load)
    }
  } catch (e) {
    error.value = (e as Error).message
    conflict.value = !!props.id && (e as { status?: number }).status === 409
  } finally { busy.value = false }
}
</script>

<template>
  <PageHeading :title="id ? '编辑转换记录' : '新建转换'" subtitle="支持 VLESS Reality、Vision、gRPC，以及节点列表、Base64 和 Clash YAML。">
    <el-button :icon="ArrowLeft" @click="router.push({ name: 'converter' })">返回在线记录</el-button>
  </PageHeading>
  <div v-if="error || conflict || (id && !record && !busy)" class="editor-notice">
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
    <el-button v-if="conflict" :disabled="busy" @click="reviewLatest">对照最新版本</el-button>
    <el-button v-if="id && !record && !busy" @click="init">重新加载</el-button>
  </div>
  <div v-if="!id || record" class="editor-layout">
    <el-form label-position="top" class="panel editor" :disabled="busy" @submit.prevent="execute('preview')">
      <el-form-item label="订阅名称" for="conversion-name">
        <input id="conversion-name" v-model="name" class="name-input" :disabled="busy" maxlength="80" placeholder="例如：手机订阅" />
      </el-form-item>
      <el-form-item label="分流模板">
        <div class="template-options" role="radiogroup" aria-label="分流模板">
          <label v-for="item in templates" :key="item.value" class="template-option" :class="{ selected: template === item.value }">
            <input v-model="template" type="radio" name="conversion-template" :value="item.value" :disabled="busy" />
            <strong>{{ item.title }}</strong>
            <span>{{ item.description }}</span>
          </label>
        </div>
      </el-form-item>
      <details v-if="existingYaml" class="fold"><summary>查看旧订阅当前配置</summary><pre class="config-preview">{{ existingYaml }}</pre></details>
      <el-form-item label="订阅内容" for="conversion-input">
        <el-input id="conversion-input" v-model="content" class="content-input" type="textarea" :rows="12" placeholder="每行一个节点链接，或粘贴 Base64 / Clash YAML 订阅内容" />
      </el-form-item>
      <input ref="filePicker" type="file" hidden accept=".yaml,.yml,.txt,.conf" @change="pick" />
      <div class="editor-actions">
        <el-button :icon="Upload" @click="filePicker?.click()">读取本地文件</el-button>
        <span class="muted">{{ lineCount ? lineCount + ' 行内容' : '支持 .yaml / .yml / .txt / .conf' }}</span>
        <el-button type="primary" native-type="submit" :icon="View" :disabled="!content.trim()" :loading="busy">预览转换</el-button>
      </div>
    </el-form>
    <aside class="panel result" aria-label="转换结果">
      <h2>转换结果</h2>
      <div v-if="!result" class="result-empty">
        <el-icon><View /></el-icon>
        <p class="muted">粘贴订阅内容并点击「预览转换」，<br />这里会显示可用节点与解析问题。</p>
      </div>
      <template v-else>
        <div class="result-stats">
          <div><span>可用节点</span><strong>{{ result.count }}</strong></div>
          <div :class="{ warn: result.issues.length }"><span>无效输入</span><strong>{{ result.issues.length }}</strong></div>
        </div>
        <p v-if="result.changes" class="result-changes">新增 {{ result.changes.added }} · 移除 {{ result.changes.removed }} · 修改 {{ result.changes.modified }}</p>
        <details v-if="result.issues.length" class="fold issues" open>
          <summary>解析问题</summary>
          <ul><li v-for="issue in result.issues" :key="issue">{{ issue }}</li></ul>
        </details>
        <el-checkbox v-if="result.issues.length && result.count" v-model="partial" :disabled="busy">跳过无效输入，使用已解析的节点</el-checkbox>
        <details v-if="result.yaml" class="fold"><summary>查看完整 YAML</summary><pre class="config-preview">{{ result.yaml }}</pre></details>
        <div v-if="result.count" class="result-actions">
          <el-button type="primary" :icon="Check" :loading="busy" :disabled="!canSave || (!!id && !name.trim())" @click="execute('save')">{{ id ? '保存到原订阅' : '发布在线订阅' }}</el-button>
          <div class="download-row">
            <el-select v-model="format" aria-label="下载格式"><el-option label="完整配置" value="full" /><el-option label="纯节点" value="nodes" /></el-select>
            <el-button :icon="Download" :disabled="!canSave || busy" @click="execute('download')">下载 YAML</el-button>
          </div>
        </div>
      </template>
    </aside>
  </div>
</template>

<style scoped>
.editor-notice { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 16px }
.editor-notice .el-alert { flex: 1 1 100% }
.editor-layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 20px; align-items: start }
.editor-layout > .panel { margin-bottom: 0 }
.editor { padding: 24px }
.name-input { width: 100%; height: 36px; padding: 0 11px; box-sizing: border-box; border-radius: var(--el-border-radius-base); border: 1px solid var(--line); background: var(--surface); color: inherit; font: inherit; font-size: 14px; outline: none; transition: border-color .15s }
.name-input:hover { border-color: var(--subtle) }
.name-input:focus { border-color: var(--accent) }
.name-input::placeholder { color: var(--subtle) }
.name-input:disabled { background: var(--surface-soft); cursor: not-allowed }
.template-options { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; width: 100% }
.template-option { position: relative; display: flex; flex-direction: column; gap: 4px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 9px; cursor: pointer; line-height: 1.5; transition: border-color .15s, background .15s }
.template-option:hover { background: var(--surface-soft) }
.template-option.selected { border-color: var(--accent); background: var(--accent-soft) }
.template-option:focus-within { outline: 2px solid var(--el-color-primary-light-5); outline-offset: 1px }
.template-option input { position: absolute; opacity: 0; pointer-events: none }
.template-option strong { font-size: 13px; font-weight: 600 }
.template-option.selected strong { color: var(--accent) }
.template-option span { color: var(--muted); font-size: 12px }
.content-input :deep(textarea) { font-family: Consolas, "SFMono-Regular", monospace; font-size: 12px; line-height: 1.7 }
.editor-actions { display: flex; align-items: center; gap: 12px; padding-top: 16px; border-top: 1px solid var(--line) }
.editor-actions .muted { flex: 1; font-size: 12px }
.editor-actions .el-button + .el-button { margin-left: 0 }
.result { position: sticky; top: 0; padding: 20px }
.result h2 { padding-bottom: 14px; margin-bottom: 16px; border-bottom: 1px solid var(--line) }
.result-empty { display: flex; flex-direction: column; align-items: center; padding: 28px 8px; text-align: center }
.result-empty .el-icon { width: 44px; height: 44px; margin-bottom: 12px; border-radius: 12px; background: var(--surface-soft); color: var(--subtle); font-size: 20px }
.result-empty p { margin: 0; font-size: 12px; line-height: 1.8 }
.result-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px }
.result-stats > div { display: flex; flex-direction: column; gap: 4px; padding: 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-soft) }
.result-stats span { color: var(--muted); font-size: 12px }
.result-stats strong { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums }
.result-stats .warn strong { color: var(--warning) }
.result-changes { margin: 12px 0 0; padding: 8px 12px; border-radius: 7px; background: var(--accent-soft); color: var(--accent); font-size: 12px }
.fold { margin: 12px 0 }
.fold summary { cursor: pointer; font-size: 13px; color: var(--muted) }
.fold summary:hover { color: var(--ink) }
.fold .config-preview { margin-top: 10px; max-height: 24rem; white-space: pre-wrap; overflow-wrap: anywhere }
.issues ul { max-height: 16rem; overflow: auto; margin: 8px 0 0; padding: 10px 10px 10px 28px; border-radius: 7px; background: var(--warning-soft); color: var(--warning); font-size: 12px; line-height: 1.8; overflow-wrap: anywhere }
.result-actions { display: grid; gap: 10px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--line) }
.result-actions > .el-button { width: 100% }
.download-row { display: flex; gap: 8px }
.download-row .el-select { flex: 1; min-width: 0 }
.download-row .el-button { margin-left: 0 }
@media (max-width: 1100px) {
  .editor-layout { grid-template-columns: 1fr }
  .result { position: static }
}
@media (max-width: 767px) {
  .editor { padding: 16px }
  .template-options { grid-template-columns: 1fr }
  .editor-actions { flex-wrap: wrap }
  .editor-actions .muted { order: 3; flex-basis: 100% }
  .name-input { height: 44px }
}
</style>
