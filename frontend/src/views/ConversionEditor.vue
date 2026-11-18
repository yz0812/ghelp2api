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
    <el-button @click="router.push({ name: 'converter' })">返回在线记录</el-button>
  </PageHeading>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
  <el-button v-if="conflict" :disabled="busy" @click="reviewLatest">对照最新版本</el-button>
  <el-button v-if="id && !record && !busy" @click="init">重新加载</el-button>
  <el-form v-if="!id || record" label-position="top" class="panel editor" :disabled="busy" @submit.prevent="execute('preview')">
    <el-form-item label="订阅名称" for="conversion-name"><input id="conversion-name" v-model="name" class="name-input" :disabled="busy" maxlength="80" placeholder="例如：手机订阅" /></el-form-item>
    <el-form-item label="分流模板" for="conversion-template">
      <el-select id="conversion-template" v-model="template">
        <el-option label="基础分流" value="basic" /><el-option label="增强分流 · Apple / Google / 广告" value="enhanced" />
      </el-select>
      <p v-if="template === 'enhanced'" class="muted">客户端会从外部规则源下载分流规则。</p>
    </el-form-item>
    <details v-if="existingYaml"><summary>查看旧订阅当前配置</summary><pre>{{ existingYaml }}</pre></details>
    <el-form-item label="订阅内容" for="conversion-input">
      <el-input id="conversion-input" v-model="content" type="textarea" :rows="8" placeholder="每行一个节点链接，或粘贴订阅内容" />
    </el-form-item>
    <input ref="filePicker" type="file" hidden accept=".yaml,.yml,.txt,.conf" @change="pick" />
    <div class="card-actions">
      <el-button @click="filePicker?.click()">读取本地文件</el-button>
      <el-button native-type="submit" :disabled="!content.trim()" :loading="busy">预览转换</el-button>
    </div>
    <section v-if="result" aria-label="转换结果" class="result">
      <h2>转换结果</h2>
      <p>{{ result.count }} 个可用节点 · {{ result.issues.length }} 条无效输入</p>
      <p v-if="result.changes">新增 {{ result.changes.added }} · 移除 {{ result.changes.removed }} · 修改 {{ result.changes.modified }}</p>
      <details v-if="result.issues.length" open><summary>解析问题</summary><ul><li v-for="issue in result.issues" :key="issue">{{ issue }}</li></ul></details>
      <el-checkbox v-if="result.issues.length && result.count" v-model="partial">跳过无效输入，使用已解析的节点</el-checkbox>
      <details v-if="result.yaml"><summary>查看完整 YAML</summary><pre>{{ result.yaml }}</pre></details>
      <div v-if="result.count" class="card-actions">
        <el-button type="primary" :disabled="!canSave || (id && !name.trim())" @click="execute('save')">{{ id ? '保存到原订阅' : '发布在线订阅' }}</el-button>
        <el-select v-model="format" aria-label="下载格式" style="width: 9rem"><el-option label="完整配置" value="full" /><el-option label="纯节点" value="nodes" /></el-select>
        <el-button :disabled="!canSave" @click="execute('download')">下载 YAML</el-button>
      </div>
    </section>
  </el-form>
</template>

<style scoped>
.name-input { width: 100%; padding: .5rem .7rem; box-sizing: border-box; border-radius: 4px; border: 1px solid var(--el-border-color); background: var(--el-fill-color-blank); color: inherit; font: inherit }
.editor { padding: 1.25rem; max-width: 64rem }
.result { margin-top: 1.25rem; border-top: 1px solid var(--el-border-color-lighter); padding-top: 1rem }
.result .card-actions { margin-top: 1rem }
details { margin: .8rem 0 }
summary { cursor: pointer }
pre, ul { max-height: 24rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere }
</style>
