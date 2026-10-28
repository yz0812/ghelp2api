<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Files, Plus, Search, SwitchButton } from '@element-plus/icons-vue'
import { api } from '../api'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import { action, busy, checkingSources, invalidSources, jobs, load, running, schedulePoll, sources } from '../store'
import { hostOf, time } from '../utils'
import type { Source } from '../types'

const visible = ref(false)
const editVisible = ref(false)
const fileVisible = ref(false)
const editing = ref<Source | null>(null)
const form = ref({ name: '', url: '', text: '', enabled: true })
const fileForm = ref({ name: '', content: '', filename: '' })
const reuploadInput = ref<HTMLInputElement>()
let reuploadTarget: Source | undefined
const checkJobId = ref('')
const checkJobFound = ref(false)

const pageSize = 9
const page = ref(1)
const paged = computed(() => sources.value.slice((page.value - 1) * pageSize, page.value * pageSize))

watch([() => sources.value.length], () => {
  page.value = Math.min(page.value, Math.max(1, Math.ceil(sources.value.length / pageSize)))
})

function openAdd() {
  form.value = { name: '', url: '', text: '', enabled: true }
  visible.value = true
}

function openEdit(source: Source) {
  editing.value = source
  form.value = { name: source.name, url: '', text: '', enabled: source.enabled }
  editVisible.value = true
}

function openFile() {
  fileForm.value = { name: '', content: '', filename: '' }
  fileVisible.value = true
}

async function save() {
  await action(async () => {
    if (editing.value) {
      await api(`/sources/${editing.value.id}`, 'PUT', {
        name: form.value.name,
        enabled: form.value.enabled,
        ...(form.value.url ? { url: form.value.url } : {})
      })
      editVisible.value = false
    } else {
      const entries = form.value.text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map((line, index) => {
        const parts = line.split(/[,，\s]+/).filter(Boolean)
        const address = parts.length > 1 ? parts.at(-1)! : line
        return { name: (parts.length > 1 ? parts.slice(0, -1).join(' ') : hostOf(address) || `订阅源 ${index + 1}`).slice(0, 80), url: address }
      })
      if (!entries.length) throw new Error('请填写至少一个订阅地址')
      const created = await api('/sources/import', 'POST', { sources: entries })
      visible.value = false
      ElMessage.success(`已添加 ${created.length} 个订阅源`)
    }
    await load()
  })
}

async function pickFile(event: Event) {
  const input = event.target as HTMLInputElement
  await action(async () => {
    const file = input.files?.[0]
    if (!file) return
    if (file.size > 10_000_000) throw new Error('文件不能超过 10 MB')
    fileForm.value.content = await file.text()
    fileForm.value.filename = file.name
    if (!fileForm.value.name.trim()) fileForm.value.name = file.name.replace(/\.(ya?ml|txt|conf)$/i, '').slice(0, 80)
  })
  input.value = ''
}

async function saveFile() {
  await action(async () => {
    const created = await api('/sources/file', 'POST', { name: fileForm.value.name.trim(), content: fileForm.value.content })
    fileVisible.value = false
    await load()
    ElMessage.success(`已导入「${created.name}」：${created.node_count} 个节点`)
  })
}

async function pull(source: Source) {
  await action(async () => {
    const result = await api(`/sources/${source.id}/refresh`, 'POST')
    await load()
    ElMessage.success(`已获取 ${result.node_count} 个节点`)
  })
}

function reupload(source: Source) { reuploadTarget = source; reuploadInput.value?.click() }

async function pickReupload(event: Event) {
  const input = event.target as HTMLInputElement
  const target = reuploadTarget
  await action(async () => {
    const file = input.files?.[0]
    if (!file || !target) return
    if (file.size > 10_000_000) throw new Error('文件不能超过 10 MB')
    const result = await api(`/sources/${target.id}/file`, 'PUT', { content: await file.text() })
    await load()
    ElMessage.success(`已更新「${result.name}」：${result.node_count} 个节点`)
  })
  input.value = ''
  reuploadTarget = undefined
}

async function remove(source: Source) {
  await action(async () => {
    await ElMessageBox.confirm(`删除「${source.name}」？使用它的订阅会移除该来源。`, '删除订阅源', { type: 'warning' })
    await api(`/sources/${source.id}`, 'DELETE')
    await load()
    ElMessage.success('订阅源已删除')
  })
}

async function check() {
  await action(async () => {
    const result = await api('/sources/check', 'POST')
    checkJobId.value = result.job_id
    checkJobFound.value = false
    await load()
    schedulePoll()
    ElMessage.info('已开始检测订阅源，完成后会提示结果')
  })
}

// 检测任务从进行中转为终态时提示结果。
watch(jobs, list => {
  if (!checkJobId.value) return
  const job = list.find(j => j.id === checkJobId.value)
  if (job) { checkJobFound.value = true; if (!['completed', 'partial', 'interrupted'].includes(job.status)) return }
  else if (!checkJobFound.value) return // 任务可能尚未进入列表，等下一轮
  checkJobId.value = ''
  const bad = invalidSources.value.length
  ElMessage({ type: bad ? 'warning' : 'success', message: bad ? `检测完成：${bad} 个订阅源失效` : '检测完成：全部订阅源正常' })
})

async function disableInvalid() {
  await action(async () => {
    await ElMessageBox.confirm(`停用 ${invalidSources.value.length} 个失效订阅源？停用后不再参与订阅生成，可随时重新启用。`, '一键禁用失效', { type: 'warning' })
    const result = await api('/sources/invalid/disable', 'POST')
    await load()
    ElMessage.success(`已停用 ${result.disabled} 个失效订阅源`)
  })
}

async function deleteInvalid() {
  await action(async () => {
    await ElMessageBox.confirm(`删除 ${invalidSources.value.length} 个失效订阅源？引用它们的订阅会移除该来源，此操作不可撤销。`, '一键删除失效', { type: 'warning' })
    const result = await api('/sources/invalid/delete', 'POST')
    await load()
    ElMessage.success(`已删除 ${result.deleted} 个失效订阅源`)
  })
}
</script>

<template>
  <PageHeading title="订阅源" subtitle="导入其他机场或自建订阅，与 GHelper 节点合并导出。">
    <el-button :icon="Files" @click="openFile">导入文件</el-button>
    <el-button type="primary" :icon="Plus" @click="openAdd">添加订阅源</el-button>
  </PageHeading>
  <RunningBanner :running="running.length > 0" />

  <div class="toolbar">
    <span class="muted">共 {{ sources.length }} 个订阅源<template v-if="invalidSources.length"> · <span class="danger-text">{{ invalidSources.length }} 个失效</span></template></span>
    <div class="toolbar-actions">
      <el-button :icon="Search" :disabled="!sources.length || checkingSources" :loading="busy" @click="check">检测失效</el-button>
      <el-button type="warning" plain :icon="SwitchButton" :disabled="!invalidSources.length || checkingSources" :loading="busy" @click="disableInvalid">一键禁用失效</el-button>
      <el-button type="danger" plain :icon="Delete" :disabled="!invalidSources.length || checkingSources" :loading="busy" @click="deleteInvalid">一键删除失效</el-button>
    </div>
  </div>

  <el-empty v-if="!sources.length" description="粘贴其他机场的订阅地址，和 GHelper 节点一起导出">
    <el-button type="primary" @click="openAdd">添加订阅源</el-button>
  </el-empty>

  <div v-else class="account-grid">
    <article v-for="s in paged" :key="s.id" :class="['panel', 'account-card', { 'is-invalid': s.invalid }]">
      <div class="card-top">
        <div class="account-avatar"><el-icon><Files /></el-icon></div>
        <div class="grow">
          <h2>{{ s.name }}</h2>
          <code class="muted">{{ s.address }}</code>
        </div>
        <el-tag :type="s.invalid ? 'danger' : s.enabled ? 'success' : 'info'">{{ s.invalid ? '已失效' : s.enabled ? '已启用' : '已停用' }}</el-tag>
      </div>
      <el-alert v-if="s.last_error" :title="s.last_error" type="warning" :closable="false" show-icon class="mb" />
      <dl class="account-details">
        <div><dt>节点数量</dt><dd>{{ s.node_count }}</dd></div>
        <div><dt>{{ s.kind === 'file' ? '导入时间' : '上次拉取' }}</dt><dd>{{ time(s.last_fetch) }}</dd></div>
        <div><dt>连续失败</dt><dd>{{ s.failures }}</dd></div>
      </dl>
      <div class="card-actions">
        <el-button v-if="s.kind === 'file'" :loading="busy" @click="reupload(s)">重新上传</el-button>
        <el-button v-else :loading="busy" :disabled="!s.enabled" @click="pull(s)">立即拉取</el-button>
        <el-button @click="openEdit(s)">编辑</el-button>
        <el-button text type="danger" @click="remove(s)">删除</el-button>
      </div>
    </article>
  </div>

  <div v-if="sources.length > pageSize" class="table-footer">
    <span>第 {{ page }} / {{ Math.ceil(sources.length / pageSize) }} 页 · 每页 {{ pageSize }} 个</span>
    <el-pagination v-model:current-page="page" :page-size="pageSize" :pager-count="5" :total="sources.length" size="small" background layout="total, prev, pager, next" />
  </div>

  <input ref="reuploadInput" type="file" accept=".yaml,.yml,.txt,.conf" hidden @change="pickReupload" />
  <div class="note">
    文件导入的订阅源不会自动更新，节点变化后点「重新上传」。检测会重新拉取每个已启用的订阅源（并发 5），最近一次拉取失败的（地址过期、内容为空、没有可解析的节点）记为失效。停用后不再参与订阅生成，重新启用即可恢复。地址只保存在本地并加密存储，列表仅显示域名。
  </div>

  <el-dialog v-model="visible" title="批量添加订阅源" width="620px" @closed="form.text = ''">
    <el-form label-position="top">
      <el-form-item label="订阅地址（每行一个，可写「名称,地址」）">
        <el-input v-model="form.text" type="textarea" :rows="9" placeholder="机场 A,https://example.com/api/v1/client/subscribe?token=...&#10;https://example.com/clash.yaml" />
      </el-form-item>
    </el-form>
    <p class="muted">支持 Clash YAML 与 base64 / 明文链接订阅（ss、vmess、trojan、vless、hysteria2）。未写名称时使用域名。地址加密保存，保存后立即拉取。</p>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!form.text.trim()" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="editVisible" title="编辑订阅源" width="620px" @closed="form.url = ''">
    <el-form label-position="top">
      <el-form-item label="名称">
        <el-input v-model="form.name" maxlength="80" />
      </el-form-item>
      <el-form-item v-if="editing?.kind === 'url'" label="订阅地址（留空保持原值）">
        <el-input v-model="form.url" type="password" show-password autocomplete="off" placeholder="https://example.com/api/v1/client/subscribe?token=..." />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editVisible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!form.name.trim()" @click="save">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="fileVisible" title="导入订阅文件" width="560px" @closed="fileForm.content = ''">
    <el-form label-position="top">
      <el-form-item label="订阅文件">
        <label class="file-picker">{{ fileForm.filename || '选择文件（Clash YAML 或链接列表）' }}<input type="file" accept=".yaml,.yml,.txt,.conf" @change="pickFile" /></label>
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="fileForm.name" maxlength="80" placeholder="默认使用文件名" />
      </el-form-item>
    </el-form>
    <p class="muted">适合订阅地址无法从服务端访问、或只有导出文件的情况。支持 Clash YAML（读取其中的 proxies）与 base64 / 明文链接列表；文件内容只保存解析后的节点，不会自动更新。</p>
    <template #footer>
      <el-button @click="fileVisible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!fileForm.content || !fileForm.name.trim()" @click="saveFile">导入</el-button>
    </template>
  </el-dialog>
</template>
