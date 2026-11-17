<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection } from '@element-plus/icons-vue'
import { error, login } from '../store'
import ThemeSwitcher from '../components/ThemeSwitcher.vue'

const router = useRouter()
const route = useRoute()
const credentials = ref({ username: 'admin', password: '' })
const busy = ref(false)

async function submit() {
  if (busy.value || !credentials.value.username.trim() || !credentials.value.password) return
  busy.value = true
  error.value = ''
  try {
    await login(credentials.value.username, credentials.value.password)
    credentials.value.password = ''
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect)
  } catch (e: any) {
    error.value = e?.message || '登录失败'
    ElMessage.error(error.value)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <div class="login-theme"><ThemeSwitcher /></div>
    <section class="login-box">
      <div class="brand"><span class="brand-mark"><el-icon><Connection /></el-icon></span>GHelper</div>
      <span class="eyebrow">YOUR CONNECTIONS, TOGETHER</span>
      <h1>登录管理台</h1>
      <p class="muted">账号、节点与订阅，在这里统一管理。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="mb" />
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="管理员账号">
          <el-input v-model="credentials.username" autocomplete="username" size="large" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="credentials.password" type="password" show-password autocomplete="current-password" size="large" />
        </el-form-item>
        <el-button type="primary" size="large" native-type="submit" class="full" :loading="busy" :disabled="!credentials.username.trim() || !credentials.password">
          登录 <el-icon><ArrowRight /></el-icon>
        </el-button>
      </el-form>
      <p class="login-foot">使用部署时设置的管理员账号登录。<br>GHelper · 个人连接管理台</p>
    </section>
  </main>
</template>
