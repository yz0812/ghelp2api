<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { api } from '../api'
import { action, load } from '../store'

const props = defineProps<{ id: string, name: string }>()
const emit = defineEmits<{ deleted: [] }>()
const deleting = ref(false)

async function remove() {
  if (deleting.value) return
  const { id, name } = props
  deleting.value = true
  try {
    await ElMessageBox.confirm(`删除「${name}」后，已分发的订阅地址将永久失效。`, '删除记录', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消'
    })
    await api('/subscriptions/' + id, 'DELETE')
    ElMessage.success(`已删除「${name}」`)
    emit('deleted')
    void action(load)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error((e as Error).message)
  } finally { deleting.value = false }
}
</script>

<template>
  <el-button type="danger" plain :icon="Delete" :loading="deleting" @click="remove"><slot>删除</slot></el-button>
</template>
