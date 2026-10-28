<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Connection } from '@element-plus/icons-vue'
import { error, login } from '../store'

const router = useRouter()
const route = useRoute()
const credentials = ref({ username: 'admin', password: '' })
const busy = ref(false)

async function submit() {
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
    <div class="login-story">
      <span class="brand"><el-icon><Connection /></el-icon> GHelper<span class="brand-small"> / WEB</span></span>
      <div>
        <span class="eyebrow">YOUR CONNECTIONS, TOGETHER</span>
        <h1>每一个连接，<br>尽在掌握。</h1>
        <p>集中管理账号与节点，<br>用一个订阅连接你的 Clash。</p>
        <div class="login-line"><span></span><el-icon><Connection /></el-icon><span></span></div>
      </div>
      <small>GHelper Clash Web · 个人连接管理台</small>
    </div>
    <div class="login-side">
      <section class="login-box">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>登录管理台</h2>
        <p class="muted">使用初始化配置中的管理员账号。</p>
        <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="mb" />
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="管理员账号">
            <el-input v-model="credentials.username" autocomplete="username" size="large" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="credentials.password" type="password" show-password autocomplete="current-password" size="large" />
          </el-form-item>
          <el-button type="primary" size="large" native-type="submit" class="full" :loading="busy">
            登录 <el-icon><ArrowRight /></el-icon>
          </el-button>
        </el-form>
        <p class="login-foot">初次使用？运行 <code>python -m app.cli init</code><br>在本机 .env 文件中查看登录信息。</p>
      </section>
    </div>
  </main>
</template>
