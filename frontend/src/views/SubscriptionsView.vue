<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Connection, Download, Link, Plus, QuestionFilled, Refresh } from '@element-plus/icons-vue'
import { api } from '../api'
import PageHeading from '../components/PageHeading.vue'
import RunningBanner from '../components/RunningBanner.vue'
import SubscriptionDetailDialog from '../components/SubscriptionDetailDialog.vue'
import { accounts, action, busy, load, regionText, regions, running, schedulePoll, settings, sources, subscriptions } from '../store'
import { copyText, downloadYaml, fetchConfig } from '../utils'
import type { Subscription } from '../types'

const route = useRoute()
const router = useRouter()
const visible = ref(false)
const previewVisible = ref(false)
const preview = ref('')
const detailVisible = ref(false)
const detailSub = ref<Subscription | null>(null)
const expandedSubs = ref<string[]>([])
const form = reactive({
  id: '',
  name: '',
  account_ids: [] as string[],
  source_ids: [] as string[],
  regions: [] as string[],
  exclude_failed: false,
  dedupe: true,
  max_latency: null as number | null,
  max_per_country: null as number | null,
  enabled: true
})

function resetForm(accountId?: string) {
  Object.assign(form, {
    id: '',
    name: accountId ? `${accounts.value.find(a => a.id === accountId)?.name || '账号'} 订阅` : '我的统一订阅',
    account_ids: accountId ? [accountId] : accounts.value.filter(a => a.enabled).map(a => a.id),
    source_ids: [],
    regions: [],
    exclude_failed: false,
    dedupe: true,
    max_latency: null,
    max_per_country: null,
    enabled: true
  })
}

function create(accountId?: string) {
  resetForm(accountId)
  visible.value = true
}

function edit(s: Subscription) {
  Object.assign(form, { ...s, account_ids: [...s.account_ids], source_ids: [...s.source_ids], regions: [...s.regions] })
  visible.value = true
}

// 从账号卡片点「生成订阅」跳转过来时，直接打开预填表单，并清掉 query 避免轮询重复触发。
watch([() => route.query.account, accounts], ([accountId]) => {
  if (typeof accountId !== 'string' || !accounts.value.length) return
  create(accountId)
  router.replace({ name: 'subscriptions' })
}, { immediate: true })

async function save() {
  await action(async () => {
    const { id, name, account_ids, source_ids, regions: regionList, exclude_failed, dedupe, max_latency, max_per_country, enabled } = form
    await api(id ? `/subscriptions/${id}` : '/subscriptions', id ? 'PUT' : 'POST', {
      name, account_ids, source_ids, regions: regionList, exclude_failed, dedupe,
      max_latency: max_latency || null, max_per_country: max_per_country || null, enabled
    })
    visible.value = false
    await load()
    ElMessage.success('订阅已保存')
  })
}

function toggleSources(id: string) {
  expandedSubs.value = expandedSubs.value.includes(id) ? expandedSubs.value.filter(x => x !== id) : [...expandedSubs.value, id]
}

function visibleSources(s: Subscription) { return expandedSubs.value.includes(s.id) ? s.source_ids : [] }

async function copy(value: string) {
  await action(async () => {
    if (!await copyText(value)) {
      preview.value = value
      previewVisible.value = true
      ElMessage.info('当前环境不支持自动复制，请手动复制')
    }
  })
}

async function showConfig(s: Subscription) {
  await action(async () => {
    preview.value = await fetchConfig(s.clash_url)
    previewVisible.value = true
  })
}

async function refresh(s: Subscription) {
  await action(async () => {
    ElMessage.info('正在重新拉取账号与订阅源…')
    const result = await api(`/subscriptions/${s.id}/refresh`, 'POST')
    ElMessage.success(result.count ? `已重新拉取，开始测速 ${result.count} 个节点；完成后订阅按规则使用新结果` : '该订阅的测速仍在进行中')
    await load()
    schedulePoll()
  })
}

async function rotate(s: Subscription) {
  await action(async () => {
    await ElMessageBox.confirm('轮换后旧订阅地址立即失效，客户端需要重新添加地址。', '轮换订阅密钥', { type: 'warning' })
    await api(`/subscriptions/${s.id}/rotate`, 'POST')
    await load()
    ElMessage.success('订阅密钥已轮换')
  })
}

async function remove(s: Subscription) {
  await action(async () => {
    await ElMessageBox.confirm(`删除「${s.name}」？原订阅地址将失效。`, '删除订阅', { type: 'warning' })
    await api(`/subscriptions/${s.id}`, 'DELETE')
    await load()
    ElMessage.success('订阅已删除')
  })
}

function openDetail(s: Subscription) {
  detailSub.value = s
  detailVisible.value = true
}

const conditionText = (s: Subscription) =>
  [s.exclude_failed ? '去除不可用' : '', s.dedupe ? '已去重' : '',
   s.max_latency ? `延迟 ≤ ${s.max_latency} ms` : '', s.max_per_country ? `每国 ≤ ${s.max_per_country} 个` : '']
    .filter(Boolean).join(' · ')
</script>

<template>
  <PageHeading title="订阅管理" subtitle="组合账号与订阅源，生成可自动更新的统一订阅。">
    <el-button type="primary" :icon="Plus" :disabled="!accounts.length && !sources.length" @click="create()">创建订阅</el-button>
  </PageHeading>
  <RunningBanner :running="running.length > 0" />

  <el-empty v-if="!subscriptions.length" description="创建第一个订阅，将节点交给 Clash 自动管理">
    <el-button type="primary" :disabled="!accounts.length && !sources.length" @click="create()">创建订阅</el-button>
  </el-empty>

  <div class="subscription-grid">
    <article v-for="s in subscriptions" :key="s.id" class="panel sub-card">
      <div class="card-top">
        <div class="account-avatar"><el-icon><Link /></el-icon></div>
        <div class="grow">
          <h2>{{ s.name }}</h2>
          <p class="muted">
            {{ s.account_ids.length }} 个账号 · {{ s.source_ids.length }} 个订阅源 ·
            {{ s.regions.length ? s.regions.map(regionText).join(' / ') : '全部入口' }}<template v-if="conditionText(s)"> · {{ conditionText(s) }}</template>
          </p>
        </div>
        <el-tag :type="s.enabled ? 'success' : 'info'">{{ s.enabled ? '已启用' : '已停用' }}</el-tag>
      </div>
      <div class="source-tags">
        <el-tag v-for="id in s.account_ids" :key="id" type="info" effect="plain">{{ accounts.find(a => a.id === id)?.name || '已删除账号' }}</el-tag>
        <el-tag v-for="id in visibleSources(s)" :key="id" type="success" effect="plain">{{ sources.find(x => x.id === id)?.name || '已删除订阅源' }}</el-tag>
        <el-button v-if="s.source_ids.length" class="tag-more" text size="small" @click="toggleSources(s.id)">
          {{ expandedSubs.includes(s.id) ? '收起来源' : `查看 ${s.source_ids.length} 个来源` }}
        </el-button>
      </div>
      <label class="field-label">Clash 完整配置</label>
      <div class="subscription-url">
        <span>{{ settings.public_base_url }}/sub/••••••••/clash.yaml</span>
        <el-button text type="primary" @click="copy(s.clash_url)">复制地址</el-button>
      </div>
      <div class="card-actions">
        <el-button type="primary" plain :icon="Connection" @click="openDetail(s)">节点详情</el-button>
        <el-button :icon="Refresh" :loading="busy" :disabled="!s.enabled" @click="refresh(s)">刷新订阅</el-button>
        <el-button :icon="Download" :disabled="!s.enabled" @click="showConfig(s)">预览 / 下载</el-button>
        <el-button text @click="edit(s)">编辑</el-button>
        <el-button text @click="rotate(s)">轮换密钥</el-button>
        <el-button text type="danger" @click="remove(s)">删除</el-button>
      </div>
    </article>
  </div>

  <div class="note">在 Clash 中新建远程订阅并粘贴地址，建议客户端每 30–60 分钟更新。订阅地址具有访问权限，请妥善保存。</div>

  <el-dialog v-model="visible" :title="form.id ? '编辑订阅' : '创建订阅'" width="720px" top="6vh" class="sub-dialog">
    <el-form label-position="top" class="sub-form">
      <div class="sub-grid">
        <el-form-item label="订阅名称"><el-input v-model="form.name" maxlength="80" /></el-form-item>
        <el-form-item label="启用订阅"><el-switch v-model="form.enabled" /></el-form-item>
      </div>
      <el-form-item label="来源账号">
        <el-select v-model="form.account_ids" multiple collapse-tags collapse-tags-tooltip :max-collapse-tags="4" placeholder="选择一个或多个 GHelper 账号" class="full">
          <el-option v-for="a in accounts" :key="a.id" :value="a.id" :label="`${a.name}${a.enabled ? '' : '（已停用）'}`" />
        </el-select>
      </el-form-item>
      <el-form-item label="订阅源（与账号至少选一个）">
        <el-select v-model="form.source_ids" multiple collapse-tags collapse-tags-tooltip :max-collapse-tags="4" placeholder="选择一个或多个外部订阅" class="full">
          <el-option v-for="s in sources" :key="s.id" :value="s.id" :label="`${s.name}${s.enabled ? '' : '（已停用）'}`" />
        </el-select>
      </el-form-item>
      <el-form-item label="入口筛选（留空包含全部）">
        <el-select v-model="form.regions" multiple filterable collapse-tags collapse-tags-tooltip :max-collapse-tags="6" class="full">
          <el-option v-for="r in regions" :key="r" :value="r" :label="regionText(r)" />
        </el-select>
      </el-form-item>
      <div class="sub-grid">
        <el-form-item>
          <template #label>
            <span class="help-label">去除不可用节点<el-tooltip content="生成时现场探测：用 mihomo 内核按节点自身配置真实访问 204 检测地址，超时或连接失败的节点不写入订阅。" placement="top" :show-after="200"><el-icon><QuestionFilled /></el-icon></el-tooltip></span>
          </template>
          <el-switch v-model="form.exclude_failed" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span class="help-label">去除重复节点<el-tooltip content="按协议、服务器、端口与凭据合并，保留先出现的那个。" placement="top" :show-after="200"><el-icon><QuestionFilled /></el-icon></el-tooltip></span>
          </template>
          <el-switch v-model="form.dedupe" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span class="help-label">最大延迟（毫秒）<el-tooltip content="留空不限制。开启后会定时测速，延迟超过上限的节点不写入订阅；未测出延迟的节点保留。" placement="top" :show-after="200"><el-icon><QuestionFilled /></el-icon></el-tooltip></span>
          </template>
          <el-input-number v-model="form.max_latency" :min="1" :max="10000" :step="50" placeholder="不限制" class="full" />
        </el-form-item>
        <el-form-item>
          <template #label>
            <span class="help-label">每国保留节点数<el-tooltip placement="top" :show-after="200" popper-class="help-tip"><template #content>留空不限制。按国家分组，只保留延迟最低的若干个节点；同一国家的多个入口（如洛杉矶、圣何塞）合并计数。<br />开启后会定时测速，没测出延迟的节点不占名额（测出结论后下一轮才参与排序），识别不出国家的节点归入「未知」单独计数。</template><el-icon><QuestionFilled /></el-icon></el-tooltip></span>
          </template>
          <el-input-number v-model="form.max_per_country" :min="1" :max="500" placeholder="不限制" class="full" />
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="(!form.account_ids.length && !form.source_ids.length) || !form.name.trim()" @click="save">保存订阅</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="previewVisible" title="配置预览" width="850px" @closed="preview = ''">
    <pre class="config-preview">{{ preview }}</pre>
    <template #footer>
      <el-button @click="copy(preview)">复制内容</el-button>
      <el-button type="primary" :icon="Download" @click="downloadYaml(preview)">下载 YAML</el-button>
    </template>
  </el-dialog>

  <SubscriptionDetailDialog v-model="detailVisible" :subscription="detailSub" />
</template>
