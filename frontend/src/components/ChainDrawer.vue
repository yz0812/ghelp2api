<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Edit, Hide, View } from '@element-plus/icons-vue'
import ChainPathPreview from './ChainPathPreview.vue'
import ChainDiagnosticPanel from './ChainDiagnosticPanel.vue'
import ChainHistoryPanel from './ChainHistoryPanel.vue'
import ProbeFeedback from './ProbeFeedback.vue'
import { api } from '../api'
import { settings } from '../store'
import { chainState, connectionFields, connectionText, copyText, relayAddress, strategyLabel } from '../utils'
import type { Chain, ConnectionInfo } from '../types'

const props = defineProps<{ modelValue: boolean; chain: Chain | null; tab: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; 'update:tab': [value: string]; refresh: []; edit: [chain: Chain] }>()
const visible = computed({ get: () => props.modelValue, set: v => emit('update:modelValue', v) })
const active = computed({ get: () => props.tab, set: v => emit('update:tab', v) })
const state = computed(() => props.chain ? chainState(props.chain) : null)
const connection = ref<ConnectionInfo | null>(null), revealed = ref(false), manualCopy = ref(''), loading = ref(false)
const fields = computed(() => connection.value ? connectionFields(connection.value) : [])
let request = 0

watch(() => [props.modelValue, props.chain?.id], ([open], previous) => {
  if (!open || previous?.[1] !== props.chain?.id) clearConnection()
})
function clearConnection() { ++request; connection.value = null; revealed.value = false; manualCopy.value = '' }
async function loadConnection(rotate = false) {
  const chain = props.chain
  if (!chain) return
  try {
    if (rotate) await ElMessageBox.confirm('轮换后原中转密码立即失效，客户端需要更新。', '轮换认证', { type: 'warning' })
    const token = ++request
    loading.value = true
    const info = await api('/chains/' + chain.id + '/' + (rotate ? 'rotate' : 'connection'), rotate ? 'POST' : 'GET')
    if (token !== request) return
    connection.value = info; manualCopy.value = ''
    if (rotate) { ElMessage.success('认证已轮换，请更新客户端'); emit('refresh') }
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally { loading.value = false }
}
async function copy(value: string) {
  if (!await copyText(value)) {
    manualCopy.value = value
    ElMessage.info('无法自动复制，请在下方选择内容并手动复制')
  }
}
/** 列表中一键复制失败时，由抽屉展示连接信息与手动复制内容。 */
function showManualCopy(info: ConnectionInfo) {
  ++request
  connection.value = info; manualCopy.value = connectionText(info); active.value = 'overview'
}
defineExpose({ showManualCopy })
</script>

<template>
  <el-drawer v-model="visible" :title="chain?.name || '链路详情'" size="min(680px, 100vw)" class="chain-drawer" append-to-body>
    <template v-if="chain && state">
      <div class="drawer-summary">
        <span :class="['state-chip', 'is-' + state.tone]"><i aria-hidden="true" />{{ state.label }}</span>
        <span class="muted">{{ strategyLabel[chain.paths?.[0]?.strategy || chain.entry_mode] || '自动选择' }} · {{ chain.relay_enabled ? '服务器中转' : '客户端原生链路' }}</span>
        <el-button class="drawer-edit" type="primary" plain :icon="Edit" @click="emit('edit', chain)">编辑链路</el-button>
      </div>
      <el-tabs v-model="active">
        <el-tab-pane label="概览" name="overview">
          <section class="drawer-section">
            <h3>配置路径</h3>
            <el-alert v-if="chain.configuration_error" :title="chain.configuration_error" type="error" :closable="false" show-icon class="mb" />
            <div v-for="(path, index) in chain.configured_paths" :key="index" class="drawer-path">
              <p><strong>{{ index === 0 ? '主路径' : '备用 ' + index }}</strong><span class="muted">{{ path.name }} · {{ strategyLabel[path.strategy] || path.strategy }}</span></p>
              <ChainPathPreview :hops="path.hops" />
            </div>
          </section>
          <section class="drawer-section">
            <h3>运行状态</h3>
            <dl v-if="chain.relay_enabled" class="kv">
              <div><dt>中转监听</dt><dd>{{ chain.status.state === 'running' ? '运行中' : chain.status.state === 'error' ? '启动失败' : chain.enabled ? '等待启动' : '已停用' }}<span v-if="chain.status.latency != null"> · {{ chain.status.latency }} ms</span></dd></div>
              <div v-if="chain.status.current_path"><dt>当前路径</dt><dd>{{ chain.status.current_path.path_name }} · {{ chain.status.current_path.hops.map(h => h.name).join(' → ') }}</dd></div>
              <div><dt>当前入口</dt><dd>{{ chain.status.current_entry || (chain.status.state === 'running' ? '暂未获取，等待状态刷新' : '中转未运行') }}</dd></div>
              <div v-if="chain.entry_mode === 'fixed' && chain.selected_entry !== undefined"><dt>指定入口</dt><dd>{{ chain.selected_entry || '节点已失效，请重新选择' }}</dd></div>
              <div v-if="chain.status.error"><dt>错误</dt><dd class="danger-text">{{ chain.status.error }}</dd></div>
            </dl>
            <p v-else class="muted">未开启服务器中转，链路只在订阅中以客户端原生多跳分发，入口由客户端测速选择。</p>
            <ProbeFeedback :result="chain.probe" :entry-label="chain.entry_mode === 'fixed' ? '本次检测指定入口' : '检测时最快入口'" />
          </section>
          <section v-if="chain.relay_enabled" class="drawer-section">
            <h3>连接信息</h3>
            <p class="muted">HTTP 与 SOCKS5 共用端口 <code>{{ relayAddress(chain, settings) }}</code>，复制内容包含完整认证信息。</p>
            <div v-if="!connection" class="toolbar-actions"><el-button type="primary" :loading="loading" @click="loadConnection()">显示连接信息</el-button></div>
            <template v-else>
              <div class="toolbar-actions mb">
                <el-button :icon="revealed ? Hide : View" @click="revealed = !revealed">{{ revealed ? '隐藏认证信息' : '显示认证信息' }}</el-button>
                <el-button type="primary" :icon="CopyDocument" @click="copy(connectionText(connection))">复制全部</el-button>
                <el-button :loading="loading" @click="loadConnection(true)">轮换认证</el-button>
              </div>
              <div v-for="field in fields" :key="field.label" class="connection-field">
                <div><span class="field-label">{{ field.label }}</span><el-button text type="primary" :aria-label="'复制' + field.label" @click="copy(field.value)">复制</el-button></div>
                <code>{{ revealed ? field.value : field.masked }}</code>
              </div>
            </template>
            <el-form-item v-if="manualCopy" label="手动复制内容" class="manual-copy"><el-input :model-value="manualCopy" type="textarea" :rows="6" readonly aria-label="手动复制内容" /></el-form-item>
          </section>
        </el-tab-pane>
        <el-tab-pane label="路径诊断" name="diagnostic" lazy>
          <ChainDiagnosticPanel :chain-id="chain.id" :active="visible && active === 'diagnostic'" @completed="emit('refresh')" />
        </el-tab-pane>
        <el-tab-pane label="监控与事件" name="history" lazy>
          <ChainHistoryPanel :chain-id="chain.id" :active="visible && active === 'history'" />
        </el-tab-pane>
      </el-tabs>
    </template>
  </el-drawer>
</template>

<style scoped>
.drawer-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-bottom: 8px; }
.drawer-edit { margin-left: auto; }
.drawer-section { padding: 4px 0 20px; }
.drawer-section + .drawer-section { padding-top: 20px; border-top: 1px solid var(--line); }
.drawer-section h3 { margin-bottom: 12px; }
.drawer-path + .drawer-path { margin-top: 14px; }
.drawer-path p { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; margin: 0 0 8px; font-size: 13px; }
.kv { margin: 0; }
.kv > div { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 12px; padding: 7px 0; font-size: 13px; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; overflow-wrap: anywhere; }
.connection-field:first-of-type { padding-top: 0; }
</style>
