<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import PageHeading from '../components/PageHeading.vue'
import { api } from '../api'
import { action, busy, chains, load, subscriptions, settings, schedulePoll } from '../store'
import { copyText } from '../utils'

type Kind = 'entries' | 'exits' | 'chains'
const router = useRouter()
const tab = ref<Kind>('chains')
const data = reactive<Record<Kind, any[]>>({ entries: [], exits: [], chains: [] })
const labels = { entries: '入口', exits: '出口', chains: '链路' }
const dialog = ref(false)
const editing = ref<Kind>('chains')
const form = reactive<Record<string, any>>({})
const nodes = ref<any[]>([])
const connection = ref('')
const connectionVisible = ref(false)
const eligible = computed(() => subscriptions.value.filter(s => s.account_ids.length || s.source_ids.length))

async function refresh() {
  const [entries, exits, chains] = await Promise.all([api('/entries'), api('/exits'), api('/chains')])
  Object.assign(data, { entries, exits, chains })
}
onMounted(() => action(refresh))
watch(chains, value => { data.chains = value })

function edit(kind: Kind, row?: any) {
  editing.value = kind
  for (const key of Object.keys(form)) delete form[key]
  Object.assign(form, kind === 'entries'
    ? { name: '', subscription_id: '', mode: 'auto', node_key: null, enabled: true }
    : kind === 'exits'
    ? { name: '', type: 'http', server: '', port: 80, username: '', password: undefined, tls: false, content: '', enabled: true }
    : { name: '', entry_id: '', exit_id: '', enabled: true, relay_enabled: true, listen_port: null, public_host: '' })
  if (row) {
    const keys = Object.keys(form)
    for (const key of keys) if (key in row) form[key] = row[key]
    form.id = row.id
  }
  dialog.value = true
}

watch(() => form.subscription_id, async id => {
  nodes.value = []
  if (!id) return
  try {
    const result = await api(`/subscriptions/${id}/nodes`)
    if (form.subscription_id === id) nodes.value = result.nodes.filter((n: any) => n.included && n.type !== "chain")
  } catch (e: any) { ElMessage.error(e.message) }
})

async function save() {
  await action(async () => {
    const { id, ...payload } = form
    if (editing.value === "exits" && id && payload.password === "") delete payload.password
    await api(`/${editing.value}${id ? `/${id}` : ''}`, id ? 'PUT' : 'POST', payload)
    dialog.value = false
    await Promise.all([refresh(), load()])
    ElMessage.success('配置已保存')
  })
}
async function remove(row: any) {
  await action(async () => {
    await ElMessageBox.confirm(`删除「${row.name}」？`, '删除配置')
    await api(`/${tab.value}/${row.id}`, 'DELETE')
    await Promise.all([refresh(), load()])
  })
}
async function showConnection(row: any, rotate = false) {
  await action(async () => {
    if (rotate) await ElMessageBox.confirm('轮换后原中转密码立即失效，客户端需要更新。', '轮换认证')
    const c = await api(`/chains/${row.id}/${rotate ? 'rotate' : 'connection'}`, rotate ? 'POST' : 'GET')
    const host = c.host.includes(':') && !c.host.startsWith('[') ? `[${c.host}]` : c.host
    connection.value = `地址：${host}:${c.port}\n账号：${c.username}\n密码：${c.password}\n\nHTTP：http://${encodeURIComponent(c.username)}:${encodeURIComponent(c.password)}@${host}:${c.port}\nSOCKS5：socks5://${encodeURIComponent(c.username)}:${encodeURIComponent(c.password)}@${host}:${c.port}`
    connectionVisible.value = true
    await refresh()
  })
}
async function probe(row: any) {
  await action(async () => {
    await api(`/chains/${row.id}/probe`, 'POST')
    await load(); schedulePoll()
    ElMessage.success('已开始整条链路检测，结果可在任务记录查看')
  })
}
async function generate(row: any) {
  await action(async () => {
    await api('/subscriptions', 'POST', { name: `${row.name} 订阅`, chain_ids: [row.id] })
    await load()
    router.push('/subscriptions')
  })
}
async function copyConnection() {
  await action(async () => { if (await copyText(connection.value)) ElMessage.success('已复制') })
}
</script>

<template>
  <PageHeading title="入口与出口" subtitle="选择订阅入口，连接出口代理，并统一管理链路与中转端口。">
    <el-button @click="action(refresh)">刷新状态</el-button>
    <el-button type="primary" @click="edit(tab)">添加{{ labels[tab] }}</el-button>
  </PageHeading>
  <div class="panel">
    <el-tabs v-model="tab">
      <el-tab-pane v-for="(label, kind) in labels" :key="kind" :label="label" :name="kind" />
    </el-tabs>
    <el-alert v-if="tab === 'chains'" title="完整订阅可使用原生两跳链路；仅节点订阅统一使用服务器中转。域名需指向本服务器，并映射对应代理端口。" type="info" :closable="false" class="mb" />
    <el-table :data="data[tab]" empty-text="暂无配置，点击右上角添加">
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column label="配置" min-width="220">
        <template #default="{ row }">
          <template v-if="tab === 'entries'">{{ subscriptions.find(s => s.id === row.subscription_id)?.name }} · {{ row.mode === 'auto' ? '自动选择' : '固定节点' }}</template>
          <template v-else-if="tab === 'exits'"><code>{{ row.server }}:{{ row.port }}</code> · {{ row.tls ? 'HTTPS' : row.type }}</template>
          <template v-else>{{ data.entries.find(e => e.id === row.entry_id)?.name }} → {{ data.exits.find(e => e.id === row.exit_id)?.name }}<div class="muted">中转端口 {{ row.listen_port }}{{ row.relay_enabled ? '' : '（中转关闭）' }}</div></template>
        </template>
      </el-table-column>
      <el-table-column label="状态" min-width="140">
        <template #default="{ row }">
          <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          <div v-if="row.status" class="muted">{{ ({ running: '监听运行中', pending: '等待启动', error: '启动失败', disabled: '中转关闭' } as Record<string, string>)[row.status.state] }}{{ row.status.latency ? ` · ${row.status.latency} ms` : '' }}<div v-if="row.status.current_entry">当前入口：{{ row.status.current_entry }}</div><div v-if="row.status.error">{{ row.status.error }}</div></div>
        </template>
      </el-table-column>
      <el-table-column label="操作" min-width="300">
        <template #default="{ row }">
          <el-button text @click="edit(tab, row)">编辑</el-button>
          <template v-if="tab === 'chains'">
            <el-button text @click="showConnection(row)">连接信息</el-button>
            <el-button text @click="probe(row)">检测</el-button>
            <el-button text @click="generate(row)">生成订阅</el-button>
            <el-button text @click="showConnection(row, true)">轮换认证</el-button>
          </template>
          <el-button text type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="muted">合并链路到已有订阅：在“订阅管理”编辑订阅，选择代理链路。{{ settings.mihomo ? '' : '当前未检测到 Mihomo，服务器中转需要安装内核。' }}</p>
  </div>
  <el-dialog v-model="dialog" :title="`${form.id ? '编辑' : '添加'}${labels[editing]}`" width="min(650px, 94vw)">
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="名称"><el-input v-model="form.name" aria-label="配置名称" maxlength="80" /></el-form-item>
      <template v-if="editing === 'entries'">
        <el-form-item label="入口来源订阅"><el-select v-model="form.subscription_id" class="full" aria-label="入口来源订阅"><el-option v-for="s in eligible" :key="s.id" :value="s.id" :label="s.name" /></el-select></el-form-item>
        <el-form-item label="选择模式"><el-radio-group v-model="form.mode"><el-radio value="auto">自动选择</el-radio><el-radio value="fixed">固定节点</el-radio></el-radio-group></el-form-item>
        <el-form-item v-if="form.mode === 'fixed'" label="固定入口节点"><el-select v-model="form.node_key" filterable class="full" aria-label="固定入口节点"><el-option v-for="n in nodes" :key="n.key" :value="n.key" :label="`${n.name} · ${n.latency ?? '未测'} ms`" /></el-select></el-form-item>
        <p class="muted">入口使用来源订阅中符合筛选条件的普通节点；自动模式检测经过出口的完整路径。</p>
      </template>
      <template v-else-if="editing === 'exits'">
        <el-form-item label="粘贴出口 YAML（可选）"><el-input v-model="form.content" type="textarea" :rows="4" placeholder="支持单节点、单节点列表或 proxies 格式" aria-label="出口 YAML" /></el-form-item>
        <template v-if="!form.content">
          <el-form-item label="协议"><el-select v-model="form.type"><el-option label="HTTP" value="http" /><el-option label="HTTPS" value="https" /><el-option label="SOCKS5" value="socks5" /></el-select></el-form-item>
          <el-form-item label="服务器域名或 IP"><el-input v-model="form.server" aria-label="出口服务器" /></el-form-item>
          <el-form-item label="端口"><el-input-number v-model="form.port" :min="1" :max="65535" /></el-form-item>
          <el-form-item label="用户名"><el-input v-model="form.username" autocomplete="off" /></el-form-item>
          <el-form-item label="密码（编辑时留空保留）"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" /></el-form-item>
          <el-form-item label="启用 TLS"><el-switch v-model="form.tls" /></el-form-item>
        </template>
      </template>
      <template v-else>
        <el-form-item label="入口"><el-select v-model="form.entry_id" class="full" aria-label="链路入口"><el-option v-for="e in data.entries" :key="e.id" :label="e.name" :value="e.id" /></el-select></el-form-item>
        <el-form-item label="出口"><el-select v-model="form.exit_id" class="full" aria-label="链路出口"><el-option v-for="e in data.exits" :key="e.id" :label="e.name" :value="e.id" /></el-select></el-form-item>
        <el-form-item label="启用服务器中转"><el-switch v-model="form.relay_enabled" /></el-form-item>
        <el-form-item label="监听端口（留空自动分配）"><el-input-number v-model="form.listen_port" :min="1024" :max="65535" /></el-form-item>
        <el-form-item label="对外域名或 IP（留空使用系统域名）"><el-input v-model="form.public_host" /></el-form-item>
      </template>
      <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
    </el-form>
    <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="busy" :disabled="!form.name?.trim()" @click="save">保存配置</el-button></template>
  </el-dialog>
  <el-dialog v-model="connectionVisible" title="代理连接信息" width="min(700px, 94vw)" @closed="connection = ''">
    <pre class="config-preview" style="white-space: pre-wrap; overflow-wrap: anywhere">{{ connection }}</pre>
    <template #footer><el-button @click="copyConnection">复制连接信息</el-button></template>
  </el-dialog>
</template>
