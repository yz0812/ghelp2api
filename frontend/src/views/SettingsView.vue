<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Monitor, Moon, Sunny } from '@element-plus/icons-vue'
import { useTheme } from '../composables/useTheme'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import PageHeading from '../components/PageHeading.vue'
import { action, busy, settings } from '../store'

const { preference, setTheme } = useTheme()
const themes = [{ value: 'light' as const, label: '浅色主题', icon: Sunny }, { value: 'dark' as const, label: '深色主题', icon: Moon }, { value: 'system' as const, label: '跟随系统', icon: Monitor }]
const form = reactive({ refresh_minutes: settings.refresh_minutes, probe_minutes: settings.probe_minutes, probe_timeout_ms: settings.probe_timeout_ms, proxy_public_host: settings.proxy_public_host })
const baseline = ref(JSON.stringify(form))
const dirty = computed(() => JSON.stringify(form) !== baseline.value)
watch(() => [settings.refresh_minutes, settings.probe_minutes, settings.probe_timeout_ms, settings.proxy_public_host], () => {
  if (dirty.value) return
  Object.assign(form, { refresh_minutes: settings.refresh_minutes, probe_minutes: settings.probe_minutes, probe_timeout_ms: settings.probe_timeout_ms, proxy_public_host: settings.proxy_public_host })
  baseline.value = JSON.stringify(form)
})

async function save() {
  if (busy.value) return
  await action(async () => {
    const saved = await api('/settings', 'PUT', {
      refresh_minutes: form.refresh_minutes,
      probe_minutes: form.probe_minutes,
      probe_timeout_ms: form.probe_timeout_ms,
      proxy_public_host: form.proxy_public_host.trim()
    })
    Object.assign(form, saved)
    Object.assign(settings, saved)
    baseline.value = JSON.stringify(form)
    ElMessage.success('设置已保存')
  })
}
</script>

<template>
  <PageHeading title="系统设置" subtitle="配置代理地址、调整获取频率，查看当前部署信息。" />

  <section class="panel settings-panel">
    <div class="settings-block">
      <h2>外观偏好</h2><p class="muted">选择适合你的界面，立即生效并保存在当前浏览器。</p>
      <div class="appearance-options" role="group" aria-label="主题偏好">
        <button v-for="theme in themes" :key="theme.value" type="button" :class="['appearance-option', { selected: preference === theme.value }]" :aria-pressed="preference === theme.value" @click="setTheme(theme.value)">
          <el-icon><component :is="theme.icon" /></el-icon>{{ theme.label }}
        </button>
      </div>
    </div>
  </section>
  <section class="panel settings-panel">
    <el-form label-position="top" @submit.prevent="save">
      <div class="settings-block">
        <h2>获取与测速</h2>
        <p class="muted">自动刷新只查询当前会话，不能强制上游签发新节点。</p>
        <div class="settings-grid">
          <el-form-item label="自动刷新间隔（分钟）">
            <el-input-number v-model="form.refresh_minutes" :min="5" :max="30" class="full" />
            <small class="field-hint">5–30 分钟</small>
          </el-form-item>
          <el-form-item label="定时测速间隔（分钟）">
            <el-input-number v-model="form.probe_minutes" :min="5" :max="60" class="full" />
            <small class="field-hint">仅对开启「去除不可用」「最大延迟」「每国保留节点数」的订阅生效</small>
          </el-form-item>
          <el-form-item label="单节点测速超时（毫秒）">
            <el-input-number v-model="form.probe_timeout_ms" :min="1000" :max="10000" :step="500" class="full" />
            <small class="field-hint">超时判为不可用；调大减少误判，但整轮更慢</small>
          </el-form-item>
        </div>
        <div class="probe-engine" :class="{ warn: !settings.mihomo }">
          <span class="dot" />
          {{ settings.mihomo ? '已启用 mihomo 内核测速：每个节点按自身配置真实访问 204 检测地址。' : '未找到 mihomo 内核，当前退回端口探测，结果仅代表端口可达。' }}
        </div>
      </div>
      <div class="settings-block">
        <h2>代理连接</h2>
        <p class="muted">为未单独指定对外地址的链路设置统一的代理域名。</p>
        <el-form-item label="代理对外域名或 IP">
          <el-input v-model="form.proxy_public_host" placeholder="proxy.accsunny.com" :maxlength="253" autocapitalize="none" :spellcheck="false" clearable />
          <small class="field-hint">只填写域名或 IP，不含协议、端口或路径。留空使用部署信息中的网站域名。</small>
        </el-form-item>
        <p class="muted">使用 Cloudflare 时，请将代理域名设为“仅 DNS（灰云）”，并确保代理端口已映射、放行。保存后新获取的连接信息和中转订阅立即生效。</p>
        <div class="settings-actions">
          <el-button type="primary" native-type="submit" :loading="busy">保存设置</el-button>
          <span v-if="dirty" class="muted"> 有尚未保存的更改</span>
        </div>
      </div>
    </el-form>
    <div class="settings-block">
      <h2>部署信息</h2>
      <dl class="info-list">
        <div>
          <dt>对外访问地址</dt>
          <dd><code>{{ settings.public_base_url }}</code><small class="field-hint">通过环境变量 PUBLIC_BASE_URL 配置；远程访问时请填写实际域名。</small></dd>
        </div>
        <div><dt>服务版本</dt><dd><code>{{ settings.version || '未知' }}</code></dd></div>
        <div><dt>兼容插件版本</dt><dd><code>{{ settings.extension_version }}</code></dd></div>
        <div><dt>API 文档</dt><dd><a href="/api/docs" target="_blank" rel="noreferrer" class="text-link">查看 API 文档 →</a></dd></div>
      </dl>
      <div class="note">数据库及加密密钥需要一起备份。管理员密码与 API 密钥通过 .env 配置，修改后重启服务。</div>
    </div>
  </section>
</template>
