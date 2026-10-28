<script setup lang="ts">
import { Connection } from '@element-plus/icons-vue'
</script>

<template>
  <main class="login-page not-found">
    <section class="login-box">
      <span class="eyebrow">404</span>
      <h2>页面不存在</h2>
      <p class="muted">这个地址没有对应的页面，可能链接已经失效。</p>
      <router-link :to="{ name: 'overview' }" class="text-link">
        <el-icon><Connection /></el-icon> 返回连接总览 →
      </router-link>
    </section>
  </main>
</template>
