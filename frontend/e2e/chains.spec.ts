import { test, expect } from '@playwright/test'

test.afterEach(async ({ request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  for (const kind of ['subscriptions', 'chains', 'entries', 'exits', 'subscriptions', 'sources']) {
    const response = await request.get(`/api/v1/${kind}`, { headers })
    for (const row of await response.json()) {
      if (/^(测试|链路测试)/.test(row.name)) await request.delete(`/api/v1/${kind}/${row.id}`, { headers })
    }
  }
})

test('链路入口支持自动与指定切换、保存回显和状态展示', async ({ page, request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  async function post(path: string, data: unknown) {
    const response = await request.post('/api/v1' + path, { headers, data })
    expect(response.ok()).toBeTruthy()
    return response.json()
  }
  const source = await post('/sources/file', {
    name: '链路测试多入口源', content: 'proxies:\n' +
      '  - {name: entry-one, type: http, server: one.example.invalid, port: 8080}\n' +
      '  - {name: entry-two, type: http, server: two.example.invalid, port: 8080}'
  })
  const subscription = await post('/subscriptions', { name: '链路测试入口订阅', source_ids: [source.id] })
  const entry = await post('/entries', { name: '链路测试候选', subscription_id: subscription.id })
  const exit = await post('/exits', { name: '链路测试出口', server: 'exit.example.invalid', port: 8080 })
  const chain = await post('/chains', { name: '链路测试选择', entry_id: entry.id, exit_id: exit.id, relay_enabled: false })
  const nodes = await (await request.get(`/api/v1/entries/${entry.id}/nodes`, { headers })).json()
  await page.goto('/login')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '链式', exact: true }).click()
  const card = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: chain.name, exact: true }) })
  await expect(card).toContainText('最快优先')
  await expect(card).toContainText('仅订阅分发')
  await card.getByRole('button', { name: '编辑', exact: true }).click()
  await expect(page).toHaveURL(new RegExp('/chains/' + chain.id + '/edit$'))
  const strategy = page.getByRole('radiogroup', { name: '入口选路策略' })
  await expect(strategy.getByRole('radio', { name: /^最快优先/ })).toHaveAttribute('aria-checked', 'true')
  await strategy.getByRole('radio', { name: /^指定节点/ }).click()
  await expect(page.getByRole('status').filter({ hasText: '有未保存修改' })).toBeVisible()
  const picker = page.getByRole('listbox', { name: '固定节点', exact: true })
  await picker.getByRole('option', { name: new RegExp(nodes[1].name) }).click()
  await expect(picker.getByRole('option', { name: new RegExp(nodes[1].name) })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('button', { name: '保存链路', exact: true }).click()
  await expect(page).toHaveURL(/\/chains$/)
  await expect(card).toContainText('指定节点')
  await page.reload()
  await expect(card).toContainText('指定节点')
  await card.getByRole('button', { name: chain.name, exact: true }).click()
  const drawer = page.locator('.chain-drawer')
  await expect(drawer).toContainText(nodes[1].name)
  await drawer.getByRole('button', { name: '编辑链路', exact: true }).click()
  await expect(strategy.getByRole('radio', { name: /^指定节点/ })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('listbox', { name: '固定节点', exact: true }).getByRole('option', { name: new RegExp(nodes[1].name) })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('status').filter({ hasText: '与已保存一致' })).toBeVisible()
  await page.getByRole('button', { name: '返回链路', exact: true }).click()
  await expect(page).toHaveURL(/\/chains$/)

  // Live core state must be distinguished from the last probe's fastest entry.
  let currentEntry = nodes[0].name
  await page.route('**/api/v1/chains', async route => {
    const response = await route.fetch()
    const rows = await response.json()
    await route.fulfill({ json: rows.map((row: { id: string }) => row.id === chain.id ? {
      ...row, relay_enabled: true, status: { state: 'running', current_entry: currentEntry, latency: 28 },
      probe: { reachable: true, latency: 20, entry: nodes[1].name, error: null, tested_at: Date.now() / 1000 }
    } : row) })
  })
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(card).toContainText('当前入口 ' + nodes[0].name)
  await expect(card.getByRole('status')).toContainText('经 ' + nodes[1].name)
  await expect(card).toContainText('运行中')
  currentEntry = nodes[1].name
  await expect(card).toContainText('当前入口 ' + nodes[1].name, { timeout: 10000 })
  await page.unroute('**/api/v1/chains')
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(card).toContainText('仅订阅分发')

  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
  await card.getByRole('button', { name: '编辑', exact: true }).click()
  await strategy.getByRole('radio', { name: /^最快优先/ }).click()
  await page.getByRole('button', { name: '保存链路', exact: true }).click()
  await expect(page).toHaveURL(/\/chains$/)
  await expect(card).toContainText('最快优先')
  const saved = await (await request.get('/api/v1/chains', { headers })).json()
  expect(saved.find((row: { id: string }) => row.id === chain.id)).toMatchObject({ entry_mode: 'auto', entry_node_key: null })
})

test('入口出口配置、原生订阅、连接信息与移动端', async ({ page, request }) => {
  const headers = { Authorization: 'Bearer e2e-synthetic-api-key-00000000000000000000' }
  const sourceResponse = await request.post('/api/v1/sources/file', { headers, data: {
    name: '链路测试入口源', content: 'proxies:\n  - {name: test-entry, type: http, server: example.invalid, port: 8080}'
  } })
  expect(sourceResponse.ok()).toBeTruthy()
  const source = await sourceResponse.json()
  const subResponse = await request.post('/api/v1/subscriptions', { headers, data: {
    name: '链路测试入口订阅', source_ids: [source.id]
  } })
  expect(subResponse.ok()).toBeTruthy()
  await page.goto('/login')
  await page.getByLabel('管理员账号').fill('admin')
  await page.getByLabel('密码', { exact: true }).fill('test-password-12345')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '链式', exact: true }).click()
  await page.getByRole('tab', { name: '入口', exact: true }).click()
  await page.getByRole('button', { name: '添加入口' }).first().click()
  await page.getByLabel('配置名称').fill('测试入口')
  await page.getByRole('combobox', { name: '入口来源订阅', exact: true }).press('Enter')
  await page.getByRole('option', { name: '链路测试入口订阅' }).click()
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('测试入口', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '出口', exact: true }).click()
  await page.getByRole('button', { name: '添加出口' }).first().click()
  await page.getByLabel('配置名称').fill('测试出口')
  await page.getByText('YAML 导入', { exact: true }).click()
  await page.getByLabel('出口 YAML').fill('- {name: synthetic, type: http, server: exit.example.invalid, port: 80, username: test, password: synthetic-only}')
  await page.getByRole('button', { name: '保存配置' }).click()
  await expect(page.getByText('测试出口', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '链路', exact: true }).click()
  await page.getByRole('button', { name: '新建链路' }).first().click()
  await expect(page).toHaveURL(/\/chains\/new$/)
  await page.getByLabel('链路名称', { exact: true }).fill('测试两跳')
  await page.getByRole('combobox', { name: '选择节点资源', exact: true }).click()
  await page.getByRole('option', { name: '测试入口', exact: true }).click()
  await page.locator('.hop-card').last().locator('.hop-select').click()
  await page.getByRole('combobox', { name: '选择节点资源', exact: true }).click()
  await page.getByRole('option', { name: '测试出口 · http', exact: true }).click()
  await expect(page.locator('.hop-card').first()).toContainText('测试入口')
  await expect(page.locator('.hop-card').last()).toContainText('测试出口')
  await page.getByRole('button', { name: '保存链路', exact: true }).click()
  await expect(page).toHaveURL(/\/chains$/)
  const chainCard = page.locator('.chain-card').filter({ has: page.getByRole('heading', { name: '测试两跳', exact: true }) })
  await expect(chainCard.locator('.path-preview')).toContainText('测试入口')
  await expect(chainCard.locator('.path-preview')).toContainText('测试出口')
  await chainCard.getByRole('button', { name: '测试两跳', exact: true }).click()
  const drawer = page.locator('.chain-drawer')
  await drawer.getByRole('button', { name: '显示连接信息', exact: true }).click()
  await expect(drawer).toContainText('SOCKS5 URI')
  await expect(drawer).not.toContainText('synthetic-only')
  await page.keyboard.press('Escape')
  await expect(drawer).not.toBeVisible()
  await chainCard.getByRole('button', { name: '更多操作', exact: true }).click()
  await page.getByRole('menuitem', { name: '生成订阅', exact: true }).click()
  await expect(page).toHaveURL(/\/subscriptions$/)
  const card = page.locator('.sub-card').filter({ has: page.getByRole('heading', { name: '测试两跳 订阅', exact: true }) })
  await expect(card).toContainText('服务器中转')
  await card.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: '编辑订阅' }).click()
  const editor = page.getByRole('dialog', { name: '编辑订阅' })
  await editor.getByRole('combobox', { name: '完整配置中的链路方式' }).press('Enter')
  await page.getByRole('option', { name: '原生链路（客户端选择候选）' }).click()
  await editor.getByRole('button', { name: '保存订阅' }).click()
  await expect(card).toContainText('原生链路')
  await card.getByRole('button', { name: '更多操作' }).click()
  await page.getByRole('menuitem', { name: '预览 / 下载' }).click()
  await expect(page.locator('.config-preview')).toContainText('dialer-proxy:')
  await page.keyboard.press('Escape')
  await page.goto('/chains')
  for (const width of [320, 390, 768]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  }
})
