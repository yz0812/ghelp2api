<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { accounts, action, busy, load } from '../store'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const visible = computed({ get: () => props.open, set: v => emit('update:open', v) })
const text = ref('')

function close() {
  visible.value = false
  text.value = ''
}

async function readFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (file.size > 1_000_000) { ElMessage.error('文件不能超过 1 MB'); input.value = ''; return }
  text.value = await file.text()
  input.value = ''
}

async function submit() {
  await action(async () => {
    const raw = text.value.trim()
    let entries: any[]
    if (raw.startsWith('{') || raw.startsWith('[')) {
      const obj = JSON.parse(raw)
      entries = Array.isArray(obj) ? obj : obj.accounts
    } else {
      entries = raw.split(/\r?\n/).filter(Boolean).map((line, i) => {
        const parts = line.trim().split(/[,，\s]+/)
        return { name: parts.length > 1 ? parts.slice(0, -1).join(' ') : `账号 ${accounts.value.length + i + 1}`, token: parts.at(-1) }
      })
    }
    if (!Array.isArray(entries) || !entries.length) throw new Error('请填写 token 或有效的导入 JSON')
    const result = await api('/accounts/import', 'POST', { accounts: entries })
    await load()
    close()
    ElMessage.success(`已添加 ${result.results.filter((x: any) => x.status === 'created').length} 个账号，重复项已跳过`)
  })
}
</script>

<template>
  <el-dialog v-model="visible" title="添加账号" width="580px" @closed="text = ''">
    <p class="muted">每行一个 token，也可填写「名称,token」或粘贴导出 JSON。</p>
    <el-input v-model="text" type="textarea" :rows="8" placeholder="账号 A,32 位 token&#10;账号 B,32 位 token" aria-label="批量 token" autocomplete="off" />
    <label class="file-picker">导入本地 JSON <input type="file" accept=".json,application/json" @change="readFile" /></label>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="submit">导入账号</el-button>
    </template>
  </el-dialog>
</template>
