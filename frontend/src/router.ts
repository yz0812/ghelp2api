import { createRouter, createWebHistory } from 'vue-router'
import { boot, loggedIn } from './store'

const routes = [
  { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue'), meta: { label: '连接总览' } },
  { path: '/accounts', name: 'accounts', component: () => import('./views/AccountsView.vue'), meta: { label: '账号管理' } },
  { path: '/nodes', name: 'nodes', component: () => import('./views/NodesView.vue'), meta: { label: '节点列表' } },
  { path: '/sources', name: 'sources', component: () => import('./views/SourcesView.vue'), meta: { label: '订阅源' } },
  { path: '/subscriptions', name: 'subscriptions', component: () => import('./views/SubscriptionsView.vue'), meta: { label: '订阅管理' } },
  { path: '/jobs', name: 'jobs', component: () => import('./views/JobsView.vue'), meta: { label: '任务记录' } },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue'), meta: { label: '系统设置' } },
  { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { label: '登录', public: true } },
  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFoundView.vue'), meta: { label: '页面不存在', public: true } }
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 }
})

router.beforeEach(async to => {
  await boot() // 首屏恢复会话；已在 boot 结束时缓存，不会重复请求。
  if (!to.meta.public && !loggedIn.value) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }
  if (to.name === 'login' && loggedIn.value) return { name: 'overview' }
})

router.afterEach(to => {
  document.title = to.meta.label ? `${to.meta.label} · GHelper` : 'GHelper · 连接管理台'
})

export default router
