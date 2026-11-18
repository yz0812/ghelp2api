import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
const prefix = '界面测试'

async function post(request: APIRequestContext, path: string, data: unknown) {
  const response = await request.post('/api/v1' + path, { headers, data })
  expect(response.ok(), path + ': ' + await response.text()).toBeTruthy()
  return response.json()
}

async function seed(request: APIRequestContext, suffix = '') {
  await post(request, '/accounts/import', { accounts: [{ name: prefix + suffix + '账号', token: 'c'.repeat(32) }] })
  const source = await post(request, '/sources/file', {
    name: prefix + suffix + '订阅源', content: 'proxies:\n  - {name: 日本入口, type: http, server: entry.example.invalid, port: 8080}'
  })
  const subscription = await post(request, '/subscriptions', { name: prefix + suffix + '基础订阅', source_ids: [source.id] })
  const entry = await post(request, '/entries', { name: prefix + suffix + '入口', subscription_id: subscription.id })
  const exit = await post(request, '/exits', {
    name: prefix + suffix + '出口', type: 'http', server: 'exit.example.invalid', port: 8080,
    username: 'synthetic-user', password: 'synthetic-password'
  })
  const chain = await post(request, '/chains', { name: prefix + suffix + '链路', entry_id: entry.id, exit_id: exit.id })
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
    const targets = [document.documentElement, document.querySelector('.content'), ...document.querySelectorAll('.el-drawer__body, .el-dialog, .el-message-box')]
    return targets.filter((el): el is HTMLElement => el instanceof HTMLElement && el.offsetWidth > 0)
      .filter(el => el.scrollWidth > el.clientWidth + 1)
      .map(el => ({ element: el.className || el.tagName, width: el.clientWidth, scrollWidth: el.scrollWidth }))
  })
  expect(overflow).toEqual([])
}

test.afterEach(async ({ request }) => {
  const subs = await (await request.get('/api/v1/subscriptions', { headers })).json()
  for (const sub of subs) if (sub.name.startsWith(prefix)) {
    await request.put('/api/v1/subscriptions/' + sub.id, { headers, data: {
      name: sub.name, account_ids: sub.account_ids, source_ids: sub.source_ids, chain_ids: []
    } })
  }
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
  await navigation.getByRole('link', { name: '链式', exact: true }).click()
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
    ['/chains', '链式'], ['/subscriptions', '订阅管理'], ['/jobs', '任务记录'], ['/settings', '系统设置']
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

test('从链式页面加入已有订阅默认使用单个中转节点', async ({ page, request }) => {
  const fixture = await seed(request)
  await login(page)
  await page.goto('/chains')
  await page.getByRole('checkbox', { name: '选择 ' + fixture.chain.name, exact: true }).press('Space')
  await page.getByRole('button', { name: '加入订阅', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '加入订阅' })
  await dialog.getByRole('combobox', { name: '目标订阅' }).press('Enter')
  await page.getByRole('option', { name: fixture.subscription.name, exact: true }).click()
  await expect(dialog).toContainText('服务器中转（每条链路一个节点）')
  await dialog.getByRole('button', { name: '保存订阅' }).click()
  await expect(dialog).not.toBeVisible()
  const subs = await (await request.get('/api/v1/subscriptions', { headers })).json()
  expect(subs.find((s: { id: string }) => s.id === fixture.subscription.id)).toMatchObject({
    chain_mode: 'relay', chain_ids: [fixture.chain.id], source_ids: [fixture.source.id], clash_url: fixture.subscription.clash_url
  })
})

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
  await page.getByRole('button', { name: fixture.chain.name, exact: true }).click()
  const connection = page.locator('.chain-drawer')
  await connection.getByRole('button', { name: '显示连接信息', exact: true }).click()
  await expect(connection).toContainText('••••••••')
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await connection.getByRole('button', { name: '复制地址', exact: true }).click()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/:\d+$/)
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }))
  await connection.getByRole('button', { name: '复制全部', exact: true }).click()
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
  await expect(card).toContainText('服务器中转')
  const subscriptions = await (await request.get('/api/v1/subscriptions', { headers })).json()
  expect(subscriptions.find((s: { id: string }) => s.id === fixture.subscription.id).chain_ids).toEqual([fixture.chain.id])
})

test('出口服务器在逐字输入、粘贴与输入法提交后失焦仍保留并可保存', async ({ page, request }) => {
  await login(page)
  await page.goto('/chains')
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  await page.getByRole('button', { name: '添加出口', exact: true }).first().click()
  const form = page.getByRole('dialog', { name: '添加出口', exact: true })
  await form.getByLabel('配置名称').fill(prefix + '输入保留')
  const server = form.getByLabel('出口服务器', { exact: true })
  await server.pressSequentially('proxy.example.invalid')
  await form.getByLabel('用户名', { exact: true }).click()
  await expect(server).toHaveValue('proxy.example.invalid')
  await server.fill('192.0.2.10')
  await server.press('Tab')
  await expect(server).toHaveValue('192.0.2.10')
  // Some IMEs commit with change before compositionend; the final DOM value
  // must survive the component's change handler restoring its controlled value.
  await server.focus()
  await server.dispatchEvent('compositionstart')
  await server.evaluate(el => {
    (el as HTMLInputElement).value = 'ime.example.invalid'
    el.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true, data: 'ime.example.invalid' }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await server.dispatchEvent('compositionend', { data: 'ime.example.invalid' })
  await server.press('Tab')
  await expect(server).toHaveValue('ime.example.invalid')
  // Autofill may emit only change. Verify both the displayed values and payload.
  for (const [label, value] of [['出口服务器', 'autofill.example.invalid'], ['用户名', 'synthetic-autofill'], ['密码（编辑时留空保留）', 'synthetic-autofill-password']]) {
    const input = form.getByLabel(label, { exact: true })
    await input.evaluate((el, text) => {
      (el as HTMLInputElement).value = text
      el.dispatchEvent(new Event('change', { bubbles: true }))
    }, value)
    await expect(input).toHaveValue(value)
  }
  await form.getByText('YAML 导入', { exact: true }).click()
  await form.getByText('手动填写', { exact: true }).click()
  await expect(server).toHaveValue('autofill.example.invalid')
  const saved = page.waitForRequest(r => r.url().endsWith('/api/v1/exits') && r.method() === 'POST')
  await form.getByRole('button', { name: '保存配置', exact: true }).click()
  expect((await saved).postDataJSON()).toMatchObject({ server: 'autofill.example.invalid', username: 'synthetic-autofill', password: 'synthetic-autofill-password' })
  await expect(form).not.toBeVisible()
  const exits = await (await request.get('/api/v1/exits', { headers })).json()
  expect(exits.find((e: { name: string }) => e.name === prefix + '输入保留').server).toBe('autofill.example.invalid')
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


test('弹窗遮罩覆盖视口且配置居中，长表单保留可见操作区', async ({ page, request }) => {
  test.setTimeout(60000)
  await seed(request)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await login(page)
  for (const theme of ['浅色', '深色']) {
    await chooseTheme(page, theme)
    for (const [width, height] of [[320, 640], [390, 800], [768, 360], [1440, 800], [2048, 800]]) {
      await page.setViewportSize({ width, height })
      for (const item of [
        { path: '/accounts', tab: '', title: '添加账号' },
        { path: '/chains', tab: '入口', title: '添加入口' },
        { path: '/chains', tab: '出口', title: '添加出口' },
        { path: '/subscriptions', tab: '', title: '创建订阅' },
      ]) {
        await page.goto(item.path)
        if (item.tab) await page.getByRole('tab', { name: item.tab, exact: true }).click()
        await page.getByRole('button', { name: item.title, exact: true }).first().click()
        const dialog = page.getByRole('dialog', { name: item.title, exact: true })
        await expect(dialog).toBeVisible()
        await expect(dialog.locator('.el-dialog')).toBeVisible()
        const bounds = await dialog.evaluate(el => {
          const overlay = el.closest('.el-overlay')!
          const box = el.querySelector('.el-dialog')!.getBoundingClientRect()
          const mask = overlay.getBoundingClientRect()
          const footer = el.querySelector('.el-dialog__footer')!.getBoundingClientRect()
          return {
            fullMask: mask.x === 0 && mask.y === 0 && mask.width === innerWidth && mask.height === innerHeight,
            centered: Math.abs(box.x + box.width / 2 - innerWidth / 2) < 2 && Math.abs(box.y + box.height / 2 - innerHeight / 2) < 2,
            footerVisible: footer.top >= box.top && footer.bottom <= innerHeight,
            rootOverlay: overlay.parentElement === document.body,
          }
        })
        expect(bounds).toEqual({ fullMask: true, centered: true, footerVisible: true, rootOverlay: true })
        await noOverflow(page)
        await dialog.getByRole('button', { name: '取消', exact: true }).click()
        await expect(dialog).not.toBeVisible()
      }
    }
  }
})


test('确认框在宽屏和手机上保持紧凑居中，长名称换行且取消不修改数据', async ({ page, request }, testInfo) => {
  test.setTimeout(60000)
  const { source } = await seed(request)
  const subscription = await post(request, '/subscriptions', {
    name: prefix + 'LongSubscriptionName'.repeat(3), source_ids: [source.id]
  })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await login(page)
  for (const theme of ['浅色', '深色']) {
    await chooseTheme(page, theme)
    for (const width of [2048, 1440, 390, 320]) {
      await page.setViewportSize({ width, height: 720 })
      await page.goto('/subscriptions')
      const card = page.locator('.sub-card').filter({ hasText: subscription.name })
      await card.getByRole('button', { name: '更多操作', exact: true }).click()
      await page.getByRole('menuitem', { name: '删除订阅', exact: true }).click()
      const box = page.locator('.el-message-box')
      await expect(box).toBeVisible()
      await expect(box).toContainText(subscription.name)
      const bounds = await box.evaluate(el => {
        const rect = el.getBoundingClientRect()
        const mask = el.closest('.el-overlay')!.getBoundingClientRect()
        const footer = el.querySelector('.el-message-box__btns')!.getBoundingClientRect()
        return {
          width: rect.width,
          centered: Math.abs(rect.x + rect.width / 2 - innerWidth / 2) < 2 && Math.abs(rect.y + rect.height / 2 - innerHeight / 2) < 2,
          contained: rect.left >= 16 && rect.right <= innerWidth - 16 && rect.top >= 16 && rect.bottom <= innerHeight - 16,
          fullMask: mask.x === 0 && mask.y === 0 && mask.width === innerWidth && mask.height === innerHeight,
          footerVisible: footer.top >= rect.top && footer.bottom <= rect.bottom,
        }
      })
      expect(bounds.width).toBeLessThanOrEqual(440)
      expect(bounds).toMatchObject({ centered: true, contained: true, fullMask: true, footerVisible: true })
      await noOverflow(page)
      if ([2048, 320].includes(width)) await page.screenshot({ path: testInfo.outputPath(`confirm-${theme}-${width}.png`) })
      await box.getByRole('button', { name: '取消', exact: true }).click()
      await expect(box).not.toBeVisible()
      await expect(card).toBeVisible()
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    for (const [path, actionName, title] of [
      ['/subscriptions', '轮换密钥', '轮换订阅密钥'],
      ['/accounts', '删除账号', '删除账号'],
      ['/sources', '删除订阅源', '删除订阅源'],
      ['/chains', '删除链路', '删除链路'],
    ]) {
      await page.goto(path!)
      await page.getByRole('button', { name: '更多操作', exact: true }).first().click()
      await page.getByRole('menuitem', { name: actionName!, exact: true }).click()
      const box = page.locator('.el-message-box')
      await expect(box).toBeVisible()
      await expect(box.locator('.el-message-box__title')).toHaveText(title!)
      expect((await box.boundingBox())!.width).toBeLessThanOrEqual(440)
      await page.keyboard.press('Escape')
      await expect(box).not.toBeVisible()
    }
  }
  const saved = await request.get('/api/v1/subscriptions', { headers })
  expect((await saved.json()).some((row: { id: string }) => row.id === subscription.id)).toBeTruthy()
})

test('浮层检查：长选项、帮助提示和长标题不超出视口', async ({ page, request }, testInfo) => {
  test.setTimeout(60000)
  const longName = prefix + 'VeryLongSourceName'.repeat(4)
  const source = await post(request, '/sources/file', { name: longName, content: 'proxies:\n  - {name: entry, type: http, server: entry.example.invalid, port: 8080}' })
  await post(request, '/subscriptions', { name: longName, source_ids: [source.id] })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await login(page)
  for (const theme of ['浅色', '深色']) {
    await chooseTheme(page, theme)
    for (const width of [320, 768, 2048]) {
      await page.setViewportSize({ width, height: 720 })
      await page.goto('/subscriptions')
      await page.getByRole('button', { name: '创建订阅', exact: true }).first().click()
      const dialog = page.getByRole('dialog', { name: '创建订阅', exact: true })
      await dialog.getByRole('combobox', { name: '订阅源', exact: true }).press('Enter')
      const option = page.getByRole('option', { name: longName, exact: true })
      await expect(option).toBeVisible()
      const selectBounds = await option.evaluate(el => {
        const box = el.closest('.el-popper')!.getBoundingClientRect()
        return { left: box.left, right: box.right }
      })
      expect.soft(selectBounds.left).toBeGreaterThanOrEqual(0)
      expect.soft(selectBounds.right).toBeLessThanOrEqual(width)
      expect.soft(await option.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy()
      await page.screenshot({ path: testInfo.outputPath(`select-${theme}-${width}.png`) })
      await option.click()
      await page.keyboard.press('Escape')
      await dialog.getByText('高级节点筛选', { exact: true }).click()
      const help = dialog.locator('.help-label').filter({ hasText: '去除不可用节点' }).locator('.el-icon')
      await help.hover()
      const tooltip = page.locator('.el-popper.is-dark:visible')
      await expect(tooltip).toBeVisible()
      const helpBox = (await tooltip.boundingBox())!
      expect.soft(helpBox.x).toBeGreaterThanOrEqual(0)
      expect.soft(helpBox.x + helpBox.width).toBeLessThanOrEqual(width)
      expect.soft(helpBox.width).toBeLessThanOrEqual(400)
      await page.screenshot({ path: testInfo.outputPath(`help-${theme}-${width}.png`) })
      await page.mouse.move(0, 0)
      await dialog.getByRole('button', { name: '取消', exact: true }).click()
      await page.locator('.sub-card').filter({ hasText: longName }).getByRole('button', { name: '节点详情', exact: true }).click()
      const detail = page.getByRole('dialog', { name: '节点详情 · ' + longName, exact: true })
      await expect(detail).toBeVisible()
      const titleFits = await detail.evaluate(el => {
        const header = el.querySelector('.el-dialog__header')!
        return header.scrollWidth <= header.clientWidth + 1
      })
      expect.soft(titleFits).toBeTruthy()
      await noOverflow(page)
      await page.screenshot({ path: testInfo.outputPath(`detail-title-${theme}-${width}.png`) })
      await page.keyboard.press('Escape')
    }
  }
})

test('浮层检查：长错误消息在手机上完整换行', async ({ page, request }, testInfo) => {
  await seed(request)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await login(page)
  await page.setViewportSize({ width: 320, height: 640 })
  await page.goto('/accounts')
  await page.route('**/api/v1/accounts/all/refresh', route => route.fulfill({ status: 500, json: { detail: '请求失败：' + 'LongUpstreamError'.repeat(20) } }))
  await page.getByRole('button', { name: '刷新全部', exact: true }).click()
  const message = page.locator('.el-message')
  await expect(message).toBeVisible()
  const fits = await message.evaluate(el => el.scrollWidth <= el.clientWidth + 1)
  expect.soft(fits).toBeTruthy()
  // Visibility can precede the toast's entrance transition finishing.
  await expect(async () => {
    const bounds = (await message.boundingBox())!
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.y).toBeGreaterThanOrEqual(0)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(320)
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(640)
  }).toPass({ timeout: 2000 })
  await page.screenshot({ path: testInfo.outputPath('long-error-320.png') })
})

test('出口测速与链路检测在当前页面等待并显示本次结果', async ({ page, request }, testInfo) => {
  const { chain, exit } = await seed(request, '实时检测')
  await login(page)
  await page.goto('/chains')
  const card = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: chain.name, exact: true }) })
  let finishChain!: () => void
  const chainGate = new Promise<void>(resolve => { finishChain = resolve })
  let chainCalls = 0
  await page.route(`**/api/v1/chains/${chain.id}/probe`, async route => {
    chainCalls++
    await chainGate
    await route.fulfill({ json: { reachable: true, latency: 42, entry: '测试最快入口', error: null, tested_at: Date.now() / 1000 } })
  })
  await card.getByRole('button', { name: '检测', exact: true }).click()
  await expect(card.getByRole('status')).toContainText('正在检测')
  await expect(card.getByRole('button', { name: '编辑', exact: true })).toBeDisabled()
  finishChain()
  await expect(card.getByRole('status')).toContainText('42 ms')
  await expect(card.getByRole('status')).toContainText('测试最快入口')
  expect(chainCalls).toBe(1)
  await page.setViewportSize({ width: 320, height: 800 })
  await noOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('live-chain-320.png') })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: exit.name, exact: true }) })
  let finishExit!: () => void
  const exitGate = new Promise<void>(resolve => { finishExit = resolve })
  await page.route(`**/api/v1/exits/${exit.id}/probe`, async route => {
    await exitGate
    await route.fulfill({ json: { reachable: false, latency: null, error: '出口直连超时，请检测对应链路', tested_at: Date.now() / 1000 } })
  })
  await row.getByRole('button', { name: '测速', exact: true }).click()
  await expect(row.getByRole('status')).toContainText('正在检测')
  finishExit()
  await expect(row.getByRole('status')).toContainText('出口直连超时')
  await page.unroute(`**/api/v1/exits/${exit.id}/probe`)
  await page.route(`**/api/v1/exits/${exit.id}/probe`, route => route.fulfill({ json: { reachable: true, latency: 88, error: null, tested_at: Date.now() / 1000 } }))
  await row.getByRole('button', { name: '测速', exact: true }).click()
  await expect(row.getByRole('status')).toContainText('最近检测可用 · 88 ms')
  await expect(row.getByRole('status')).not.toContainText('超时')
  await noOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('live-exit-1440.png') })
})

test('链路卡片一键复制连接信息并支持失败后手动复制', async ({ page, request, context }) => {
  const { chain } = await seed(request, '一键复制')
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await login(page)
  await page.goto('/chains')
  const card = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: chain.name, exact: true }) })
  const info = { host: '2001:db8::1', port: 20000, username: 'test@user', password: 'test:/#密码', protocols: ['http', 'socks5'] }
  let calls = 0
  await page.route(`**/api/v1/chains/${chain.id}/connection`, route => {
    calls++
    return route.fulfill({ json: info })
  })
  await card.getByRole('button', { name: '复制连接信息', exact: true }).click()
  const expected = 'http://' + encodeURIComponent(info.username) + ':' + encodeURIComponent(info.password) + '@[2001:db8::1]:20000'
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain(expected)
  const copied = await page.evaluate(() => navigator.clipboard.readText())
  expect(copied).toContain('密码：' + info.password)
  expect(copied).toContain('SOCKS5 URI：socks5://')
  await expect(page.locator('.chain-drawer')).not.toBeVisible()
  await expect(card).not.toContainText(info.password)
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined }))
  await card.getByRole('button', { name: '复制连接信息', exact: true }).click()
  const dialog = page.locator('.chain-drawer')
  await expect(dialog.getByLabel('手动复制内容', { exact: true })).toHaveValue(copied.replace(/\r\n/g, '\n'))
  expect(calls).toBe(2)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await page.setViewportSize({ width: 320, height: 900 })
  await noOverflow(page)
})

test('地区国旗统一使用本地图片并保留入口名称和未知地区回退', async ({ page, request }, testInfo) => {
  await seed(request, '地区国旗')
  await login(page)
  await page.goto('/nodes')
  const china = page.locator('.el-table__body .region-badge[data-country="CN"]').first()
  await expect(china).toContainText('广州移动')
  await expect(china.locator('img')).toBeVisible()
  await expect.poll(() => china.locator('img').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBeTruthy()
  const usa = page.locator('.el-table__body .region-badge[data-country="US"]').first()
  await expect(usa).toContainText('洛杉矶')
  await expect(usa.locator('img')).toBeVisible()
  await page.getByRole('combobox', { name: '筛选入口', exact: true }).press('Enter')
  const option = page.getByRole('option', { name: '广州移动', exact: true })
  await expect(option.locator('img')).toBeVisible()
  await option.click()
  await expect(option).not.toBeVisible()
  await expect(page.locator('.el-table__body .region-badge')).toHaveCount(1)
  await expect(page.locator('.el-select__selected-item .region-badge img')).toBeVisible()
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await noOverflow(page)
    await page.screenshot({ path: testInfo.outputPath('region-flags-' + width + '.png') })
  }
  // A running backend without --reload can still return the old response shape.
  await page.route('**/api/v1/nodes', async route => {
    const response = await route.fetch()
    const nodes = await response.json()
    await route.fulfill({ json: nodes.map(({ region_country, region_name, ...node }: Record<string, unknown>) => node) })
  })
  await page.reload()
  await expect(china).toContainText('广州移动')
  await expect.poll(() => china.locator('img').evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBeTruthy()
  await expect(china.locator('img')).toHaveAttribute('src', /^\/assets\/cn-.*\.svg$/)
  await page.getByRole('combobox', { name: '筛选入口', exact: true }).press('Enter')
  await expect(page.getByRole('option', { name: '广州移动', exact: true }).locator('img')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.screenshot({ path: testInfo.outputPath('region-flags-legacy-api.png') })
  await page.unroute('**/api/v1/nodes')
  await page.route('**/api/v1/nodes', async route => {
    const response = await route.fetch()
    const nodes = await response.json()
    await route.fulfill({ json: [{ ...nodes[0], region: 'ZZ9', region_country: 'UN', region_name: 'ZZ9', region_label: 'ZZ9' }] })
  })
  await page.reload()
  const unknown = page.locator('.el-table__body .region-badge[data-country="UN"]')
  await expect(unknown).toContainText('ZZ9')
  await expect(unknown.locator('svg')).toBeVisible()
  await expect(unknown.locator('img')).toHaveCount(0)
})

test('检测结果在刷新页面和重新进入后保持显示', async ({ page, request }) => {
  const { chain, exit } = await seed(request, '持久缓存')
  // The isolated test server has no core; the actual API persists the failure.
  await post(request, `/chains/${chain.id}/probe`, {})
  await post(request, `/exits/${exit.id}/probe`, {})
  await login(page)
  await page.goto('/chains')
  const card = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: chain.name, exact: true }) })
  await expect(card.getByRole('status')).toContainText('检测失败')
  await page.reload()
  await expect(card.getByRole('status')).toContainText('检测失败')
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  const row = page.getByRole('row').filter({ has: page.getByRole('cell', { name: exit.name, exact: true }) })
  await expect(row.getByRole('status')).toContainText('缺少 Mihomo')
  await page.goto('/subscriptions')
  await page.goto('/chains')
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  await expect(row.getByRole('status')).toContainText('最近检测失败')
})

test('入口节点独立弹窗支持测速、排序、错误重试和取消草稿', async ({ page, request }) => {
  const { entry, subscription } = await seed(request)
  const result = await (await request.get(`/api/v1/subscriptions/${subscription.id}/nodes`, { headers })).json()
  const node = result.nodes[0]
  let probeReads = 0
  let started = false
  let fail = false
  const batches: string[][] = []
  await page.route(`**/api/v1/subscriptions/${subscription.id}/nodes`, route => {
    const pending = started && ++probeReads === 1
    return route.fulfill({ json: { nodes: [{ ...node, latency: started && !pending ? 25 : null, reachable: started && !pending ? true : null }], job_id: pending ? 'synthetic-probe-job' : null } })
  })
  await page.route(`**/api/v1/subscriptions/${subscription.id}/probe`, route => {
    batches.push(route.request().postDataJSON().keys)
    if (fail) return route.fulfill({ status: 503, json: { detail: '模拟测速失败，请重试' } })
    started = true; probeReads = 0
    return route.fulfill({ status: 202, json: { count: 1, job_id: 'synthetic-probe-job' } })
  })
  await login(page)
  await page.goto('/chains')
  await page.getByRole('tab', { name: '入口', exact: true }).click()
  await page.getByRole('row').filter({ has: page.getByRole('cell', { name: entry.name, exact: true }) }).getByRole('button', { name: '编辑', exact: true }).click()
  const form = page.getByRole('dialog', { name: '编辑入口', exact: true })
  await form.getByText('选择候选节点（可多选）', { exact: true }).click()
  await form.getByRole('button', { name: '选择节点', exact: true }).click()
  const picker = page.getByRole('dialog', { name: '选择入口节点', exact: true })
  await picker.getByRole('checkbox', { name: node.name, exact: true }).check()
  await picker.getByLabel('搜索入口节点').fill(node.server)
  await picker.getByRole('button', { name: '测速已选 (1)', exact: true }).click()
  await expect(picker.getByText('正在测速，结果自动更新…')).toBeVisible()
  await expect(picker.getByRole('button', { name: '测速全部 (1)', exact: true })).toBeDisabled()
  await expect(picker.getByText('25 ms', { exact: true })).toBeVisible()
  await expect(picker.getByLabel('搜索入口节点')).toHaveValue(node.server)
  await expect(picker.getByRole('checkbox')).toBeChecked()
  await picker.getByRole('combobox', { name: '节点排序', exact: true }).press('Enter')
  await page.getByRole('option', { name: '延迟从低到高', exact: true }).click()
  fail = true
  await picker.getByRole('button', { name: '测速全部 (1)', exact: true }).click()
  await expect(picker.getByRole('alert')).toContainText('模拟测速失败')
  await expect(picker.getByRole('checkbox')).toBeChecked()
  fail = false
  await picker.getByRole('button', { name: '测速全部 (1)', exact: true }).click()
  await expect(picker.getByText('正在测速，结果自动更新…')).toBeVisible()
  await expect(picker.getByText('25 ms', { exact: true })).toBeVisible()
  expect(batches).toEqual([[node.key], [node.key], [node.key]])
  await page.keyboard.press('Escape')
  await expect(picker).not.toBeVisible()
  await expect(form).toBeVisible()
  await expect(form).toContainText('已选 0 个节点')
  await form.getByRole('button', { name: '选择节点', exact: true }).click()
  await expect(picker.getByRole('checkbox')).not.toBeChecked()
  await picker.getByRole('checkbox').check()
  await picker.getByRole('button', { name: '确认选择 (1)', exact: true }).click()
  await expect(form).toContainText('已选 1 个节点')
})

test('固定入口节点列表支持搜索、分页、保存回显和手机布局', async ({ page, request }, testInfo) => {
  const source = await post(request, '/sources/file', {
    name: prefix + '多节点来源',
    content: 'proxies:\n' + Array.from({ length: 8 }, (_, i) =>
      `  - {name: ${i < 4 ? '香港' : '日本'}节点${i + 1}, type: http, server: entry${i + 1}.example.invalid, port: 8080}`).join('\n')
  })
  const subscription = await post(request, '/subscriptions', { name: prefix + '多节点订阅', source_ids: [source.id] })
  const response = await request.get('/api/v1/subscriptions/' + subscription.id + '/nodes', { headers })
  const { nodes } = await response.json()
  await login(page)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/chains')
  await page.getByRole('tab', { name: '入口', exact: true }).click()
  await page.getByRole('button', { name: '添加入口', exact: true }).first().click()
  const form = page.getByRole('dialog', { name: '添加入口', exact: true })
  await form.getByLabel('配置名称').fill(prefix + '固定入口')
  await form.getByText('选择候选节点（可多选）', { exact: true }).click()
  await expect(form).toContainText('请先选择入口来源订阅')
  await form.getByRole('combobox', { name: '入口来源订阅', exact: true }).press('Enter')
  await page.getByRole('option', { name: subscription.name, exact: true }).click()
  await form.getByRole('button', { name: '选择节点', exact: true }).click()
  const selection = page.getByRole('dialog', { name: '选择入口节点', exact: true })
  const picker = selection.getByRole('region', { name: '固定入口节点', exact: true })
  await expect(picker.getByRole('checkbox')).toHaveCount(6)
  await expect(form.getByRole('button', { name: '保存配置' })).toBeDisabled()
  await picker.getByRole('checkbox', { name: nodes[0].name, exact: true }).check()
  await picker.locator('.btn-next').click()
  await expect(picker.getByRole('checkbox')).toHaveCount(2)
  await picker.getByRole('checkbox', { name: nodes[6].name, exact: true }).check()
  await expect(picker.getByRole('status')).toContainText('已选 2 个节点')
  await expect(picker.getByRole('status')).toContainText(nodes[0].name)
  const regions = picker.getByRole('group', { name: '按地区筛选' })
  await regions.getByRole('button', { name: /日本/ }).click()
  await expect(regions.getByRole('button', { name: /日本/ }).locator('img')).toBeVisible()
  await expect(picker.getByRole('checkbox')).toHaveCount(4)
  await expect(picker.getByRole('checkbox', { name: nodes[6].name, exact: true })).toBeChecked()
  await regions.getByRole('button', { name: /香港/ }).click()
  await expect(picker.getByRole('checkbox')).toHaveCount(4)
  await expect(picker.getByRole('checkbox', { name: nodes[0].name, exact: true })).toBeChecked()
  await expect(picker.getByRole('status')).toContainText('已选 2 个节点')
  await regions.getByRole('button', { name: /全部地区/ }).click()

  await picker.getByLabel('搜索入口节点').fill(nodes[0].server)
  await expect(picker.getByRole('checkbox')).toHaveCount(1)
  await expect(picker.getByRole('checkbox')).toBeChecked()
  await expect(picker).toContainText(nodes[0].server + ':8080')
  await expect(picker).toContainText('HTTP')
  await expect(picker).toContainText('未测速')
  await picker.getByLabel('搜索入口节点').fill('不存在的节点')
  await expect(picker).toContainText('没有匹配的节点')
  await picker.getByRole('button', { name: '清空搜索' }).click()
  await expect(picker.getByRole('checkbox')).toHaveCount(6)
  for (const width of [320, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    await picker.scrollIntoViewIfNeeded()
    await noOverflow(page)
    await page.screenshot({ path: testInfo.outputPath('node-picker-' + width + '.png') })
  }
  await selection.getByRole('button', { name: '确认选择 (2)', exact: true }).click()
  await expect(selection).not.toBeVisible()
  await form.getByRole('button', { name: '保存配置' }).click()
  await expect(form).not.toBeVisible()
  const entries = await (await request.get('/api/v1/entries', { headers })).json()
  expect(entries.find((e: { name: string }) => e.name === prefix + '固定入口')).toMatchObject({ mode: 'fixed', node_keys: [nodes[0].key, nodes[6].key] })
  await page.getByRole('row').filter({ hasText: prefix + '固定入口' }).getByRole('button', { name: '编辑', exact: true }).click()
  const edited = page.getByRole('dialog', { name: '编辑入口' })
  await expect(edited).toContainText('已选 2 个节点')
  await edited.getByRole('button', { name: '选择节点', exact: true }).click()
  await expect(selection.getByRole('checkbox', { name: nodes[0].name, exact: true })).toBeChecked()
  await selection.locator('.btn-next').click()
  await expect(selection.getByRole('checkbox', { name: nodes[6].name, exact: true })).toBeChecked()
  await selection.getByRole('button', { name: '清空选择', exact: true }).click()
  await expect(selection.getByRole('button', { name: '确认选择 (0)', exact: true })).toBeDisabled()
  await selection.getByRole('button', { name: '取消', exact: true }).click()
  await expect(edited).toContainText('已选 2 个节点')
  await expect(edited.getByRole('button', { name: '保存配置', exact: true })).toBeEnabled()
})
