import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
const prefix = '界面测试'

async function post(request: APIRequestContext, path: string, data: unknown) {
  const response = await request.post('/api/v1' + path, { headers, data })
  expect(response.ok(), path + ': ' + await response.text()).toBeTruthy()
  return response.json()
}

async function seed(request: APIRequestContext) {
  await post(request, '/accounts/import', { accounts: [{ name: prefix + '账号', token: 'c'.repeat(32) }] })
  const source = await post(request, '/sources/file', {
    name: prefix + '订阅源', content: 'proxies:\n  - {name: 日本入口, type: http, server: entry.example.invalid, port: 8080}'
  })
  const subscription = await post(request, '/subscriptions', { name: prefix + '基础订阅', source_ids: [source.id] })
  const entry = await post(request, '/entries', { name: prefix + '入口', subscription_id: subscription.id })
  const exit = await post(request, '/exits', {
    name: prefix + '出口', type: 'http', server: 'exit.example.invalid', port: 8080,
    username: 'synthetic-user', password: 'synthetic-password'
  })
  const chain = await post(request, '/chains', { name: prefix + '链路', entry_id: entry.id, exit_id: exit.id })
  await post(request, '/accounts/all/refresh', {})
  return { source, subscription, entry, exit, chain }
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '连接总览', exact: true })).toBeVisible()
}

async function chooseTheme(page: Page, label: string) {
  await page.getByRole('button', { name: '切换外观' }).click()
  await page.getByRole('menuitem', { name: new RegExp('^' + label) }).click()
  await expect(page.getByRole('menu')).not.toBeVisible()
}

async function noOverflow(page: Page) {
  const overflow = await page.evaluate(() => {
    const targets = [document.documentElement, document.querySelector('.content'), ...document.querySelectorAll('.el-drawer__body, .el-dialog')]
    return targets.filter((el): el is HTMLElement => el instanceof HTMLElement && el.offsetWidth > 0)
      .filter(el => el.scrollWidth > el.clientWidth + 1)
      .map(el => ({ element: el.className || el.tagName, width: el.clientWidth, scrollWidth: el.scrollWidth }))
  })
  expect(overflow).toEqual([])
}

test.afterEach(async ({ request }) => {
  for (const kind of ['subscriptions', 'chains', 'entries', 'exits', 'subscriptions', 'sources', 'accounts']) {
    const response = await request.get('/api/v1/' + kind, { headers })
    for (const row of await response.json()) {
      if (row.name.startsWith(prefix)) await request.delete('/api/v1/' + kind + '/' + row.id, { headers })
    }
  }
})

test('主题持久化、系统跟随、禁用存储及键盘导航', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
  await page.goto('/login')
  await expect(page.locator('html')).toHaveClass('dark')
  await chooseTheme(page, '浅色')
  await expect(page.locator('html')).not.toHaveClass('dark')
  await login(page)
  await page.reload()
  await expect(page.locator('html')).not.toHaveClass('dark')
  await page.goto('/settings')
  await page.getByRole('button', { name: '深色主题', exact: true }).click()
  await expect(page.locator('html')).toHaveClass('dark')
  await page.reload()
  await expect(page.getByRole('button', { name: '深色主题' })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: '跟随系统', exact: true }).click()
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).not.toHaveClass('dark')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(page.locator('html')).toHaveClass('dark')

  await page.setViewportSize({ width: 390, height: 844 })
  const toggle = page.getByRole('button', { name: '打开导航' })
  await toggle.focus()
  await page.keyboard.press('Enter')
  const navigation = page.getByRole('dialog', { name: '工作空间' })
  await expect(navigation).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(navigation).not.toBeVisible()
  await expect(toggle).toBeFocused()
  await toggle.click()
  await navigation.getByRole('link', { name: '入口与出口', exact: true }).click()
  await expect(page).toHaveURL(/\/chains$/)
  await expect(navigation).not.toBeVisible()

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: '收起侧栏' }).click()
  await page.reload()
  await expect(page.getByRole('button', { name: '展开侧栏' })).toBeVisible()
  await page.getByRole('button', { name: '展开侧栏' }).click()

  await page.addInitScript(() => {
    Storage.prototype.getItem = () => { throw new Error('Storage disabled') }
    Storage.prototype.setItem = () => { throw new Error('Storage disabled') }
  })
  await page.reload()
  await chooseTheme(page, '浅色')
  await expect(page.locator('html')).not.toHaveClass('dark')
})

for (const theme of ['浅色', '深色']) {
test('全部页面在' + theme + '主题与四种宽度下布局正常', async ({ page, request }, testInfo) => {
  test.setTimeout(60000)
  await seed(request)
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await login(page)
  const routes: [string, string][] = [
    ['/', '连接总览'], ['/accounts', '账号管理'], ['/nodes', '节点列表'], ['/sources', '订阅源'],
    ['/chains', '入口与出口'], ['/subscriptions', '订阅管理'], ['/jobs', '任务记录'], ['/settings', '系统设置']
  ]
  await chooseTheme(page, theme)
    for (const width of [320, 390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 })
      for (const [path, title] of routes) {
        if (width < 768) await page.getByRole('button', { name: '打开导航' }).click()
        await page.getByRole('link', { name: title, exact: true }).click()
        if (width < 768) await expect(page.getByRole('dialog', { name: '工作空间' })).not.toBeVisible()
        await expect(page).toHaveURL(path === '/' ? /\/$/ : new RegExp(path + '$'))
        await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
        await noOverflow(page)
        if ([390, 1440].includes(width)) {
          await page.screenshot({ path: testInfo.outputPath(theme + '-' + width + '-' + (path.slice(1) || 'overview') + '.png') })
        }
      }
      if (width < 768) await page.getByRole('button', { name: '打开导航' }).click()
      await page.getByRole('link', { name: '订阅管理', exact: true }).click()
      if (width < 768) await expect(page.getByRole('dialog', { name: '工作空间' })).not.toBeVisible()
      await page.getByRole('button', { name: '创建订阅', exact: true }).first().click()
      await expect(page.getByRole('dialog', { name: '创建订阅' })).toBeVisible()
      await noOverflow(page)
      await page.keyboard.press('Escape')
      await page.getByRole('button', { name: '节点详情' }).first().click()
      await expect(page.getByRole('dialog', { name: /节点详情/ })).toBeVisible()
      await expect(page.getByRole('cell', { name: prefix + '订阅源-日本入口', exact: true })).toBeVisible()
      await noOverflow(page)
      await page.keyboard.press('Escape')
    }
  await page.getByRole('button', { name: '退出', exact: true }).click()
    await chooseTheme(page, theme)
    await page.setViewportSize({ width: 390, height: 844 })
    await noOverflow(page)
    await page.screenshot({ path: testInfo.outputPath(theme + '-login.png') })
    await page.goto('/does-not-exist')
    await expect(page.getByRole('heading', { name: '页面不存在' })).toBeVisible()
    await noOverflow(page)
    await page.goto('/login')
  expect(errors).toEqual([])
})
}

test('出口表单保留草稿与密码、连接复制回退、订阅合并链路', async ({ page, request, context }) => {
  const fixture = await seed(request)
  await login(page)
  await page.goto('/chains')
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  await page.getByRole('row').filter({ hasText: prefix + '出口' }).getByRole('button', { name: '编辑', exact: true }).click()
  const form = page.getByRole('dialog', { name: '编辑出口' })
  await form.getByLabel('出口服务器').fill('updated.example.invalid')
  await form.getByText('YAML 导入', { exact: true }).click()
  await form.getByLabel('出口 YAML', { exact: true }).fill('name: 未保存的 YAML 草稿')
  await form.getByText('手动填写', { exact: true }).click()
  await expect(form.getByLabel('出口服务器')).toHaveValue('updated.example.invalid')

  await page.route('**/api/v1/exits/' + fixture.exit.id, route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 422, json: { detail: '模拟保存失败，请重试' } }) : route.continue())
  await form.getByRole('button', { name: '保存配置' }).click()
  await expect(form.getByRole('alert')).toContainText('模拟保存失败')
  await expect(form.getByLabel('出口服务器')).toHaveValue('updated.example.invalid')
  await page.unroute('**/api/v1/exits/' + fixture.exit.id)
  const saved = page.waitForRequest(r => r.url().endsWith('/exits/' + fixture.exit.id) && r.method() === 'PUT')
  await form.getByRole('button', { name: '保存配置' }).click()
  const payload = (await saved).postDataJSON()
  expect(payload).not.toHaveProperty('password')
  expect(payload).not.toHaveProperty('content')
  await expect(form).not.toBeVisible()
  const exits = await (await request.get('/api/v1/exits', { headers })).json()
  expect(exits.find((e: { id: string }) => e.id === fixture.exit.id)).toMatchObject({ server: 'updated.example.invalid', has_password: true })

  await page.getByRole('tab', { name: '链路', exact: true }).click()
  await page.getByRole('button', { name: '连接信息', exact: true }).click()
  const connection = page.getByRole('dialog', { name: '代理连接信息' })
  await expect(connection).toContainText('••••••••')
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await connection.getByRole('button', { name: '复制地址', exact: true }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/:\d+$/)
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }))
  await connection.getByRole('button', { name: '复制连接信息', exact: true }).click()
  await expect(connection.getByLabel('手动复制内容', { exact: true })).toHaveValue(/SOCKS5 URI：socks5:\/\//)
  await page.keyboard.press('Escape')

  await page.goto('/subscriptions')
  const card = page.locator('.sub-card').filter({ hasText: prefix + '基础订阅' })
  await card.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: '编辑订阅', exact: true }).click()
  const subscription = page.getByRole('dialog', { name: '编辑订阅' })
  await subscription.getByRole('combobox', { name: '代理链路', exact: true }).press('Enter')
  await page.getByRole('option', { name: prefix + '链路', exact: true }).click()
  await page.keyboard.press('Escape')
  await subscription.getByRole('button', { name: '保存订阅' }).click()
  await expect(subscription).not.toBeVisible()
  await expect(card).toContainText('1 条链路')
  await expect(card).toContainText('原生链路')
  const subscriptions = await (await request.get('/api/v1/subscriptions', { headers })).json()
  expect(subscriptions.find((s: { id: string }) => s.id === fixture.subscription.id).chain_ids).toEqual([fixture.chain.id])
})

test('资源筛选、失效搜索空状态与设置草稿保护', async ({ page, request }) => {
  await seed(request)
  await login(page)
  await page.goto('/accounts')
  await page.getByRole('textbox', { name: '搜索账号', exact: true }).fill('不存在的账号')
  await expect(page.getByRole('heading', { name: '没有符合条件的账号' })).toBeVisible()
  await page.getByRole('button', { name: '清空筛选' }).click()
  await expect(page.getByRole('heading', { name: prefix + '账号', exact: true })).toBeVisible()
  await page.goto('/sources')
  await page.getByRole('textbox', { name: '搜索订阅源', exact: true }).fill('不存在的来源')
  await expect(page.getByRole('heading', { name: '没有符合条件的订阅源' })).toBeVisible()
  await page.getByRole('button', { name: '清空筛选' }).click()
  await expect(page.getByRole('heading', { name: prefix + '订阅源', exact: true })).toBeVisible()
  await page.goto('/jobs')
  await page.getByRole('combobox', { name: '任务类型' }).press('Enter')
  await page.getByRole('option', { name: '账号刷新', exact: true }).click()
  await expect(page.getByRole('cell', { name: '账号刷新', exact: true }).first()).toBeVisible()
  await page.goto('/settings')
  const interval = page.getByRole('spinbutton', { name: '自动刷新间隔（分钟）' })
  await interval.fill('12')
  await interval.blur()
  await expect(page.getByText('有尚未保存的更改')).toBeVisible()
  // 快进定时器触发轮询，确认服务端新设置不会覆盖正在编辑的草稿。
  await request.put('/api/v1/settings', { headers, data: { refresh_minutes: 20, probe_minutes: 30, probe_timeout_ms: 5000 } })
  await page.clock.install()
  await page.clock.runFor(16000)
  await expect(interval).toHaveValue('12')
  await page.getByRole('button', { name: '保存设置', exact: true }).click()
  await expect(page.getByText('有尚未保存的更改')).not.toBeVisible()
  await page.reload()
  await expect(interval).toHaveValue('12')
})
