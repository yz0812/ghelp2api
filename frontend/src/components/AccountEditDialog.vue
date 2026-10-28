<script setup lang="ts">
import { reactive, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { action, busy, load } from '../store'
import type { Account } from '../types'

const props = defineProps<{ open: boolean, account: Account | null }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const form = reactive({ name: '', token: '', enabled: true })

watch(() => props.account, account => {
  if (account) Object.assign(form, { name: account.name, token: '', enabled: account.enabled })
}, { immediate: true })

async function submit() {
  if (!props.account) return
  await action(async () => {
    await api(`/accounts/${props.account!.id}`, 'PATCH', {
      name: form.name,
      enabled: form.enabled,
      ...(form.token ? { token: form.token } : {})
    })
    emit('update:open', false)
    await load()
    ElMessage.success('账号已更新')
  })
}
</script>

<template>
  <el-dialog :model-value="open" title="编辑账号" width="500px" @update:model-value="emit('update:open', $event)" @closed="form.token = ''">
    <el-form label-position="top">
      <el-form-item label="账号名称">
        <el-input v-model="form.name" maxlength="80" />
      </el-form-item>
      <el-form-item label="替换 token（留空保持原值）">
        <el-input v-model="form.token" type="password" show-password autocomplete="off" />
      </el-form-item>
      <el-form-item label="参与刷新和订阅">
        <el-switch v-model="form.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:open', false)">取消</el-button>
      <el-button type="primary" :loading="busy" :disabled="!form.name.trim()" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>
